"""Conservative, bounded AST discovery. Never import or execute target modules."""
import ast
import copy
import hashlib
import os
import re
import stat
import uuid
from itertools import islice
from pathlib import Path
from .config import AppError
from .storage import digest

VERSION = "code-3"
EXCLUDED = {".git", ".venv", "venv", "env", "node_modules", "site-packages", "__pycache__", "build", "dist", "output", "outputs", ".tox", ".idea", "secrets", "credentials", "tests", "migrations", "alembic"}
SECRET_NAME = re.compile(r"(?:secret|credential|password|passwd|private_key|api_key|access_token|refresh_token)", re.I)
METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
PEP758_EXCEPT = re.compile(r"^(\s*except\*?[ \t]+)((?:[A-Za-z_][\w.]*[ \t]*,[ \t]*)+[A-Za-z_][\w.]*)([ \t]*:.*)$")
SETTING_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,99}$")
PATH_PREFIX = re.compile(r"^(?:/[A-Za-z0-9._~-]+)*$")
AUTH_LIFECYCLE = re.compile(r"(?:login|logout|password|passwd|token|signup|register|auth)", re.I)
EMAIL_TYPES = {"pydantic.EmailStr", "pydantic.networks.EmailStr"}
SESSION_TYPES = {"sqlmodel.Session", "sqlalchemy.orm.Session", "sqlalchemy.ext.asyncio.AsyncSession", "sqlmodel.ext.asyncio.session.AsyncSession"}
SECURITY_SCHEMES = {"fastapi.security." + n for n in ("OAuth2PasswordBearer", "OAuth2AuthorizationCodeBearer", "HTTPBearer", "HTTPBasic", "HTTPDigest", "APIKeyHeader", "APIKeyQuery", "APIKeyCookie", "OpenIdConnect")}
CREDENTIAL_FORMS = {"fastapi.security.OAuth2PasswordRequestForm", "fastapi.security.OAuth2PasswordRequestFormStrict"}
USER_KINDS = {"authenticated_user", "role_check"}


def parse_source(text, rel):
    """Parse with the running interpreter. Only Python 3.14 (PEP 758) unparenthesized except
    clauses are parenthesized in memory on the same line, so line numbers stay exact."""
    lines, adapted = text.split("\n"), []
    while True:
        try:
            return ast.parse("\n".join(lines), filename=rel), adapted
        except SyntaxError as exc:
            index = (exc.lineno or 0) - 1
            match = PEP758_EXCEPT.match(lines[index]) if exc.msg == "multiple exception types must be parenthesized" and 0 <= index < len(lines) and index + 1 not in adapted else None
            if not match: raise
            lines[index] = match[1] + "(" + match[2] + ")" + match[3]
            adapted.append(index + 1)


def validate_setting_values(raw):
    """Owner-confirmed, non-secret settings used only to resolve route prefixes."""
    values = {}
    for name, value in (raw or {}).items():
        name, value = str(name).strip(), str(value).strip()
        if not SETTING_KEY.match(name) or SECRET_NAME.search(name) or len(value) > 200 or not PATH_PREFIX.match(value):
            raise AppError("setting_confirmation", "Confirmed settings must be non-secret NAME=/path/prefix values", details={"name": name[:100]})
        values[name] = value
    if len(values) > 20:
        raise AppError("setting_confirmation", "At most 20 confirmed settings are accepted")
    return dict(sorted(values.items()))


def sensitive(schema):
    return isinstance(schema, dict) and (bool(schema.get("x-sensitive")) or sensitive(schema.get("items")) or any(sensitive(v) for v in schema.get("properties", {}).values()))


def scalar_default(node):
    try: value = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, RecursionError): return False, None
    return (value is None or isinstance(value, (bool, int, float, str))), value


def bounded_path(raw, allowed):
    if not allowed:
        raise AppError("project_directory_configuration", "Set ALLOWED_PROJECT_DIRECTORY before discovering local projects", 503)
    try:
        base = Path(allowed)
        target = Path(raw.strip())
        if not base.is_absolute() or not target.is_absolute():
            raise AppError("project_path", "Use an absolute path on the backend filesystem")
        # Check lexical containment as well as resolved containment; no links/junctions.
        lexical = Path(os.path.abspath(target))
        base = Path(os.path.abspath(base))
        if not lexical.is_relative_to(base):
            raise AppError("project_boundary", "Project path is outside ALLOWED_PROJECT_DIRECTORY", 403)
        for part in [*reversed(base.parents), base, *reversed(list(lexical.parents)[:len(lexical.parts)-len(base.parts)]), lexical]:
            if part.exists() and (part.is_symlink() or getattr(part, "is_junction", lambda: False)()):
                raise AppError("project_link", "Symlink or junction project paths are unsupported", 403)
        resolved = lexical.resolve(strict=True)
        if not resolved.is_relative_to(base.resolve(strict=True)):
            raise AppError("project_boundary", "Resolved project path escapes the allowed directory", 403)
        if not resolved.is_dir():
            raise AppError("project_path", "Project path must be a directory")
        return resolved
    except (OSError, ValueError) as exc:
        raise AppError("project_inaccessible", "Project directory does not exist or is inaccessible on this backend", details={"path":raw}) from exc


def index_project(raw, settings):
    root = bounded_path(raw, settings.allowed_project_directory)
    files, diagnostics, manifest = {}, [], []
    counts = dict(entries=0, files=0, bytes=0)
    limited = False
    def issue(code, path):
        diagnostics.append(dict(code=code, severity="warning", pointer=path, message=code.replace("_", " ")))
    def visit(folder):
        nonlocal limited
        if limited:
            return
        try:
            with os.scandir(folder) as entries:
                remaining=settings.code_max_entries-counts["entries"]
                children=list(islice(entries,remaining+1))
                if len(children)>remaining:
                    limited=True;issue("entry_limit",str(Path(folder).relative_to(root)));return
                children=sorted(children,key=lambda e:e.name)
        except OSError:
            issue("directory_inaccessible", str(Path(folder).relative_to(root)))
            return
        for entry in children:
            counts["entries"] += 1
            if counts["entries"] > settings.code_max_entries:
                limited = True; issue("entry_limit", "."); return
            path = Path(entry.path); rel = path.relative_to(root).as_posix()
            if entry.name.lower() in EXCLUDED or entry.name.startswith(".") or SECRET_NAME.search(entry.name):
                continue
            try:
                metadata = entry.stat(follow_symlinks=False)
                if entry.is_symlink() or getattr(path, "is_junction", lambda:False)():
                    issue("link_excluded", rel); continue
                if stat.S_ISDIR(metadata.st_mode):
                    visit(path); continue
                if not stat.S_ISREG(metadata.st_mode) or path.suffix != ".py":
                    continue
                if counts["files"] >= settings.code_max_files:
                    limited = True; issue("file_limit", rel); return
                if metadata.st_size > settings.code_max_file_bytes:
                    manifest.append((rel, "oversize", metadata.st_size, metadata.st_mtime_ns)); issue("file_size_limit", rel); continue
                if counts["bytes"] + metadata.st_size > settings.code_max_total_bytes:
                    limited = True; issue("total_bytes_limit", rel); return
                # Recheck real path and link status immediately before bounded reading.
                if path.is_symlink() or not path.resolve(strict=True).is_relative_to(root):
                    issue("link_excluded", rel); continue
                metadata=path.stat(follow_symlinks=False)
                with path.open("rb") as handle:
                    raw_bytes = handle.read(settings.code_max_file_bytes + 1)
                    opened=os.fstat(handle.fileno())
                after=path.stat(follow_symlinks=False)
                if (opened.st_dev,opened.st_ino)!=(metadata.st_dev,metadata.st_ino) or (after.st_dev,after.st_ino)!=(opened.st_dev,opened.st_ino) or (after.st_size,after.st_mtime_ns)!=(metadata.st_size,metadata.st_mtime_ns) or path.is_symlink() or not path.resolve(strict=True).is_relative_to(root):
                    issue("source_changed_during_read",rel);continue
                if len(raw_bytes) > settings.code_max_file_bytes:
                    issue("file_size_limit", rel); continue
                sha = hashlib.sha256(raw_bytes).hexdigest()
                manifest.append((rel, sha)); counts["files"] += 1; counts["bytes"] += len(raw_bytes)
                try:
                    text = raw_bytes.decode("utf-8-sig")
                    tree, adapted = parse_source(text, rel)
                except SyntaxError as exc:
                    diagnostics.append(dict(code="python_parse_error", severity="warning", pointer=f"{rel}:{exc.lineno or 1}", message=f"python parse error: {exc.msg}")); continue
                except (ValueError, UnicodeError, RecursionError):
                    issue("python_parse_error", rel); continue
                for line in adapted:
                    diagnostics.append(dict(code="python_grammar_adapted", severity="info", pointer=f"{rel}:{line}", message="Python 3.14 (PEP 758) unparenthesized except clause parenthesized in memory for this parser; source bytes, hash and line numbers unchanged"))
                files[rel] = dict(tree=tree, sha256=sha, lines=text.splitlines())
            except OSError:
                issue("file_inaccessible", rel)
    visit(root)
    config = {k:getattr(settings,k) for k in ("code_max_files","code_max_entries","code_max_file_bytes","code_max_total_bytes","code_max_snippets","code_max_context_chars","code_exploration_steps","max_operations")}
    snapshot = digest(dict(version=VERSION, config=config, manifest=manifest, diagnostics=diagnostics))
    return dict(root=str(root), files=files, diagnostics=diagnostics, counts=counts, snapshot=snapshot, config=config)


def dotted(node):
    if isinstance(node, ast.Name): return node.id
    if isinstance(node, ast.Attribute): return dotted(node.value) + "." + node.attr
    return ""


def kw(call, name):
    return next((k.value for k in call.keywords if k.arg == name), None)


class SanitizedCode(ast.NodeTransformer):
    def visit_Constant(self, node):
        if isinstance(node.value, str): return ast.copy_location(ast.Constant("[string omitted]"), node)
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool): return ast.copy_location(ast.Constant(0), node)
        return node
    def visit_Expr(self, node):
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str): return None
        return self.generic_visit(node)


class Inspector:
    def __init__(self, indexed, business_id, settings, setting_values=None):
        self.indexed, self.business_id, self.settings = indexed, business_id, settings
        self.units, self.aliases, self.evidence, self.operations = {}, {}, {}, []
        self.setting_values, self.settings_seen, self.confirmed_used = setting_values or {}, {}, set()
        self.diagnostics = list(indexed["diagnostics"])
        for rel, data in indexed["files"].items():
            stem = rel[:-3].replace("/", ".")
            if stem.endswith(".__init__"): stem = stem[:-9]
            self.units[rel] = dict(**data, module=stem, symbols={}, imports={}, routers={}, includes=[], conditional=[])
            for name in (stem, stem.removeprefix("src."), stem.split(".app.")[-1] if ".app." in stem else stem):
                self.aliases.setdefault(name, []).append(rel)
            if ".app." in stem:
                self.aliases.setdefault("app."+stem.split(".app.",1)[1], []).append(rel)
        for rel, unit in self.units.items():
            for node in unit["tree"].body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    unit["symbols"][node.name] = node
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node,ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target,ast.Name): unit["symbols"][target.id] = node.value
                elif isinstance(node,ast.ImportFrom):
                    module = node.module or ""
                    if node.level:
                        owner = unit["module"].split(".")
                        if not rel.endswith("__init__.py"): owner.pop()
                        module = ".".join(owner[:len(owner)-node.level+1] + ([module] if module else []))
                    for item in node.names: unit["imports"][item.asname or item.name] = module + "." + item.name
                elif isinstance(node,ast.Import):
                    for item in node.names: unit["imports"][item.asname or item.name.split(".")[0]] = item.name
        for rel, unit in self.units.items():
            for name, node in unit["symbols"].items():
                if isinstance(node,ast.Call) and self.name(rel,node.func).split(".")[-1] in {"FastAPI","APIRouter"} and self.name(rel,node.func).startswith("fastapi."):
                    prefix_node = kw(node,"prefix")
                    prefix = [] if prefix_node is None else self.parts(rel,prefix_node)
                    if any(k.arg is None or k.arg in {"route_class","default_response_class","responses","callbacks","routes"} for k in node.keywords):
                        self.issue("unsupported_router_constructor",rel,node);prefix=None
                    literal = "".join(prefix) if prefix is not None and all(isinstance(p,str) for p in prefix) else None
                    if literal and (not literal.startswith("/") or literal.endswith("/")):
                        self.issue("invalid_router_prefix",rel,node);prefix=None
                    unit["routers"][name] = dict(kind=self.name(rel,node.func).split(".")[-1], prefix=prefix, node=node)
            for node in unit["tree"].body:
                if isinstance(node,ast.Expr) and isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Attribute) and node.value.func.attr=="include_router":
                    unit["includes"].append(node.value)
                elif isinstance(node,ast.If):
                    unit["conditional"] += [n for n in ast.walk(node) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=="include_router"]

    def issue(self, code, rel, node=None, message=None):
        self.diagnostics.append(dict(code=code, severity="warning", pointer=f"{rel}:{getattr(node,'lineno',1)}", message=message or code.replace("_"," ")))

    def name(self, rel, node):
        text = dotted(node); first, _, rest = text.partition(".")
        return self.units[rel]["imports"].get(first,first) + ("."+rest if rest else "")

    def lookup(self, rel, node):
        text = dotted(node)
        if text in self.units[rel]["symbols"]: return rel,text,self.units[rel]["symbols"][text]
        full = self.name(rel,node)
        module, _, symbol = full.rpartition(".")
        candidates = self.aliases.get(module, [])
        if len(set(candidates))==1:
            target=candidates[0]
            if symbol in self.units[target]["symbols"]: return target,symbol,self.units[target]["symbols"][symbol]
        return None

    def literal(self, rel, node, depth=0):
        if node is None or depth>8: return None
        try: return ast.literal_eval(node)
        except (ValueError,TypeError):
            found=self.lookup(rel,node)
            if found and found[2] is not node: return self.literal(found[0],found[2],depth+1)
        return None

    def parts(self, rel, node, depth=0):
        """Path pieces: literal strings and settings references; None when not statically known."""
        if node is None or depth>8: return None
        if isinstance(node,ast.Constant): return [node.value] if isinstance(node.value,str) else None
        if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Add):
            left,right=self.parts(rel,node.left,depth+1),self.parts(rel,node.right,depth+1)
            return None if left is None or right is None else left+right
        if isinstance(node,ast.JoinedStr):
            result=[]
            for value in node.values:
                if isinstance(value,ast.Constant) and isinstance(value.value,str): piece=[value.value]
                elif isinstance(value,ast.FormattedValue) and value.conversion==-1 and value.format_spec is None: piece=self.parts(rel,value.value,depth+1)
                else: piece=None
                if piece is None: return None
                result+=piece
            return result
        if isinstance(node,(ast.Name,ast.Attribute)):
            setting=self.setting_ref(rel,node)
            if setting: return [setting]
            found=self.lookup(rel,node)
            if found and found[2] is not node: return self.parts(found[0],found[2],depth+1)
        return None

    def setting_ref(self, rel, node):
        """A field of a module-level pydantic BaseSettings instance. Settings code is never executed."""
        if not isinstance(node,ast.Attribute): return None
        instance=self.lookup(rel,node.value)
        if not instance or not isinstance(instance[2],ast.Call) or instance[2].args or instance[2].keywords: return None
        found=self.lookup(instance[0],instance[2].func)
        if not found or not isinstance(found[2],ast.ClassDef): return None
        owner,class_name,cls=found
        if not any(self.name(owner,b) in {"pydantic_settings.BaseSettings","pydantic.BaseSettings"} for b in cls.bases): return None
        field=next((f for f in cls.body if isinstance(f,ast.AnnAssign) and isinstance(f.target,ast.Name) and f.target.id==node.attr),None)
        if field is None: return None
        key=(owner,class_name,node.attr)
        if key not in self.settings_seen:
            ok,default=scalar_default(field.value) if field.value is not None else (False,None)
            disclosed=ok and isinstance(default,str) and PATH_PREFIX.match(default) and not SECRET_NAME.search(node.attr)
            self.settings_seen[key]=dict(setting=node.attr,settings_class=class_name,file=owner,line=field.lineno,has_code_default=field.value is not None,code_default=default if disclosed else None,evidence_ids=[self.evidence_for(owner,field,class_name+"."+node.attr),self.evidence_for(instance[0],instance[2],instance[1])])
        return self.settings_seen[key]

    def resolve_parts(self, parts):
        """Join path parts. Settings resolve only through explicit owner confirmation, never code defaults."""
        text,used,missing="",[],[]
        for part in parts:
            if isinstance(part,str): text+=part;continue
            confirmed=self.setting_values.get(part["setting"])
            entry=dict(part,value_source="owner_confirmed" if confirmed is not None else "unconfirmed",confirmed_value=confirmed,matches_code_default=None if confirmed is None or part["code_default"] is None else confirmed==part["code_default"])
            if confirmed is None: missing.append(part)
            else: text+=confirmed;self.confirmed_used.add(part["setting"])
            used.append(entry)
        return (None if missing else text),used,missing

    def resolve_router(self, rel, node, depth=0):
        found=self.lookup(rel,node) if node is not None and depth<8 else None
        if not found: return None
        router=self.units[found[0]]["routers"].get(found[1])
        if router and router["node"] is found[2]: return found[0],found[1]
        if isinstance(found[2],(ast.Name,ast.Attribute)) and found[2] is not node: return self.resolve_router(found[0],found[2],depth+1)
        return None

    def evidence_for(self, rel, node, symbol):
        data=self.units[rel]
        entry=dict(file=rel,symbol=symbol,line_start=node.lineno,line_end=node.end_lineno,source_hash=data["sha256"],snapshot_id=self.indexed["snapshot"])
        eid=digest(entry)
        if eid not in self.evidence:
            try: snippet=ast.unparse(SanitizedCode().visit(copy.deepcopy(node)))
            except (ValueError,RecursionError): snippet="[AST rendering unavailable]"
            self.evidence[eid]=dict(id=eid,**entry,sanitized_code=snippet[:3000],snippet_truncated=len(snippet)>3000)
        return eid

    def schema(self, rel, annotation, stack=()):
        if annotation is None: raise ValueError("Missing declared type")
        text=self.name(rel,annotation)
        primitive={"str":"string","int":"integer","float":"number","bool":"boolean"}
        if text in primitive:
            if text in self.units[rel]["symbols"]:raise ValueError("Primitive type name is shadowed")
            return {"type":primitive[text]},[]
        if text in {"uuid.UUID","datetime.datetime","datetime.date"}:
            return dict(type="string",format={"uuid.UUID":"uuid","datetime.datetime":"date-time","datetime.date":"date"}[text]),[]
        if text in EMAIL_TYPES:
            return {"type":"string","format":"email","x-python-type":"pydantic.EmailStr"},[]
        if isinstance(annotation,ast.Subscript):
            container=self.name(rel,annotation.value).split(".")[-1]
            args=list(annotation.slice.elts) if isinstance(annotation.slice,ast.Tuple) else [annotation.slice]
            if container=="Annotated": return self.schema(rel,args[0],stack)
            if container in {"list","List"}:
                item,ev=self.schema(rel,args[0],stack);return dict(type="array",items=item),ev
            if container=="Optional":
                item,ev=self.schema(rel,args[0],stack);return dict(item,nullable=True),ev
            if container=="Literal":
                values=[self.literal(rel,a) for a in args]
                if not values or any(type(v) is not type(values[0]) for v in values): raise ValueError("Unresolved literal enum")
                type_name={str:"string",int:"integer",bool:"boolean"}.get(type(values[0]))
                if not type_name: raise ValueError("Unsupported enum")
                return dict(type=type_name,enum=values),[]
        if isinstance(annotation,ast.BinOp) and isinstance(annotation.op,ast.BitOr):
            if isinstance(annotation.right,ast.Constant) and annotation.right.value is None:
                item,ev=self.schema(rel,annotation.left,stack);return dict(item,nullable=True),ev
            raise ValueError("Union types are unsupported")
        found=self.lookup(rel,annotation)
        if not found or not isinstance(found[2],ast.ClassDef): raise ValueError("Type is unresolved or unsupported: "+(text or ast.dump(annotation)[:80]))
        owner,symbol,node=found
        key=(owner,symbol)
        if key in stack or len(stack)>8: raise ValueError("Recursive model is unsupported")
        props,required,evidence={},[],[self.evidence_for(owner,node,symbol)]
        recognized=False
        for base in node.bases:
            base_name=self.name(owner,base)
            if base_name in {"pydantic.BaseModel","sqlmodel.SQLModel"}: recognized=True
            else:
                parent,ev=self.schema(owner,base,(*stack,key)); recognized=True
                props.update(parent["properties"]);required.extend(parent.get("required",[]));evidence+=ev
        if not recognized: raise ValueError("Class is not a supported declared model")
        if any(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) for n in node.body):
            raise ValueError("Models with custom methods/validators are unsupported")
        for field in node.body:
            if not isinstance(field,ast.AnnAssign) or not isinstance(field.target,ast.Name): continue
            name=field.target.id
            child,ev=self.schema(owner,field.annotation,(*stack,key));evidence+=ev
            credential=bool(SECRET_NAME.search(name))
            if credential: child={**child,"x-sensitive":"credential"}
            default=field.value
            needed=default is None
            selected=default
            if isinstance(default,ast.Call):
                if self.name(owner,default.func).split(".")[-1]!="Field": raise ValueError("Dynamic model default is unsupported")
                selected=default.args[0] if default.args else kw(default,"default")
                needed=selected is None or (isinstance(selected,ast.Constant) and selected.value is Ellipsis)
                if kw(default,"default_factory") is not None: needed=False
                if kw(default,"alias") is not None or kw(default,"validation_alias") is not None: raise ValueError("Model aliases are unsupported")
                for source,target in {"min_length":"minLength","max_length":"maxLength","ge":"minimum","le":"maximum","pattern":"pattern"}.items():
                    value=kw(default,source)
                    if value is not None:
                        literal=self.literal(owner,value)
                        if literal is None: raise ValueError("Dynamic field constraint")
                        child[target]=literal
                if any(k.arg not in {"default","default_factory","description","title","min_length","max_length","ge","le","pattern","primary_key","foreign_key","index","nullable","unique"} for k in default.keywords): raise ValueError("Unsupported model Field option")
            if selected is not None and not needed and not credential:
                ok,value=scalar_default(selected)
                if ok: child["default"]=value
            props[name]=child
            if name in required: required.remove(name)
            if needed: required.append(name)
        return dict(type="object",properties=props,required=sorted(set(required))),evidence

    def dependency(self, rel, node, depth=0):
        """Return (module, Depends/Security call, Annotated base type or None)."""
        if node is None or depth>8:return None
        if isinstance(node,ast.Subscript) and self.name(rel,node.value).split(".")[-1]=="Annotated":
            args=list(node.slice.elts) if isinstance(node.slice,ast.Tuple) else []
            found=next((a for a in args[1:] if isinstance(a,ast.Call) and self.name(rel,a.func) in {"fastapi.Depends","fastapi.Security"}),None)
            return (rel,found,args[0]) if found else None
        if isinstance(node,ast.Call) and self.name(rel,node.func) in {"fastapi.Depends","fastapi.Security"}: return rel,node,None
        found=self.lookup(rel,node) if isinstance(node,(ast.Name,ast.Attribute)) else None
        if found and found[2] is not node and isinstance(found[2],(ast.Subscript,ast.Name,ast.Attribute)): return self.dependency(found[0],found[2],depth+1)
        return None

    def param_dependency(self, rel, arg, default):
        dep=self.dependency(rel,default)
        if dep: return dep[0],dep[1],dep[2] if dep[2] is not None else arg.annotation
        return self.dependency(rel,arg.annotation)

    def checks(self, rel, fn, users, every_raise):
        """Observed conditional HTTP errors and user-dependent branches. Not verified authorization."""
        def http_error(statements):
            for stmt in statements:
                if isinstance(stmt,ast.Raise) and isinstance(stmt.exc,ast.Call) and self.name(rel,stmt.exc.func).split(".")[-1]=="HTTPException":
                    code=stmt.exc.args[0] if stmt.exc.args else kw(stmt.exc,"status_code")
                    value=self.literal(rel,code) if code is not None else None
                    if not isinstance(value,int):
                        match=re.search(r"HTTP_(\d{3})_",dotted(code) if code is not None else "")
                        value=int(match[1]) if match else None
                    return dict(status=value)
            return None
        def text(node):
            try: return ast.unparse(SanitizedCode().visit(copy.deepcopy(node)))[:200]
            except (ValueError,RecursionError): return "[condition unavailable]"
        def is_user(node):
            return isinstance(node,ast.Name) and node.id in users or isinstance(node,ast.Attribute) and node.attr=="id" and isinstance(node.value,ast.Name) and node.value.id in users
        found=[]
        for node in ast.walk(fn):
            if isinstance(node,ast.If):
                error=http_error(node.body)
                attrs=sorted({n.attr for n in ast.walk(node.test) if isinstance(n,ast.Attribute) and isinstance(n.value,ast.Name) and n.value.id in users}-{"id"})
                compares_user=any(isinstance(n,ast.Compare) and any(is_user(x) for x in [n.left,*n.comparators]) for n in ast.walk(node.test))
                about_user=bool(attrs) or compares_user
                if error and compares_user: kind="ownership_check"
                elif error and attrs and error["status"] in (401,403): kind="role_check"
                elif error and (every_raise or about_user or error["status"] in (401,403)): kind="raise_check"
                elif not error and about_user: kind="conditional_branch"
                else: continue
                found.append(dict(kind=kind,condition=text(node.test),status=error["status"] if error else None,line=node.lineno,user_attributes=attrs))
            elif isinstance(node,ast.ExceptHandler):
                error=http_error(node.body)
                if error: found.append(dict(kind="raise_check",condition="except "+(text(node.type) if node.type else "any exception"),status=error["status"],line=node.lineno,user_attributes=[]))
        return found[:12]

    def describe_dependency(self, owner, call, base, op, required_by, depth=0):
        """Flatten one declared dependency and its sub-dependencies into op['dependencies']."""
        if not isinstance(call,ast.Call):
            op["access"]["unresolved"].append("Unresolved dependency declaration");return dict(kind="unresolved")
        expr=call.args[0] if call.args else kw(call,"dependency")
        if expr is None: expr=base
        name=self.name(owner,expr) if expr is not None else "unresolved"
        target=self.lookup(owner,expr) if isinstance(expr,(ast.Name,ast.Attribute)) else None
        existing=next((d for d in op["dependencies"] if (d.get("file"),d["symbol"])==((target[0],target[1]) if target else (None,name))),None)
        if existing:
            if required_by not in existing["required_by"]: existing["required_by"].append(required_by)
            return existing
        item=dict(symbol=target[1] if target else name,kind="unclassified",required_by=[required_by],meaning="Static observation; runtime authentication/permission effect is unverified",checks=[],requires=[],evidence_ids=[])
        if target: item["file"]=target[0]
        op["dependencies"].append(item)
        scopes=kw(call,"scopes")
        if scopes is not None:
            value=self.literal(owner,scopes)
            if isinstance(value,list) and all(isinstance(v,str) for v in value):item["declared_scopes"]=value
            else:item["unresolved_scopes"]=True;self.issue("dynamic_dependency_scopes",owner,call)
        if name in CREDENTIAL_FORMS:
            item["kind"]="credential_form"
        elif target and isinstance(target[2],ast.Call) and self.name(target[0],target[2].func) in SECURITY_SCHEMES:
            item.update(kind="security_scheme",scheme=self.name(target[0],target[2].func).split(".")[-1],evidence_ids=[self.evidence_for(target[0],target[2],target[1])])
        elif target and isinstance(target[2],(ast.FunctionDef,ast.AsyncFunctionDef)) and depth<4:
            t_rel,t_symbol,t_fn=target
            item["evidence_ids"]=[self.evidence_for(t_rel,t_fn,t_symbol)]
            defaults=[None]*(len(t_fn.args.args)-len(t_fn.args.defaults))+list(t_fn.args.defaults)
            users,kinds=set(),set()
            for arg,default in list(zip(t_fn.args.args,defaults))+list(zip(t_fn.args.kwonlyargs,t_fn.args.kw_defaults)):
                sub=self.param_dependency(t_rel,arg,default)
                if not sub: continue
                child=self.describe_dependency(*sub,op,name,depth+1)
                kinds.add(child["kind"])
                if child.get("symbol"): item["requires"].append(child["symbol"])
                if child["kind"] in USER_KINDS: users.add(arg.arg)
            item["checks"]=self.checks(t_rel,t_fn,users,True)
            session=(base is not None and self.name(owner,base) in SESSION_TYPES) or any(isinstance(n,ast.Call) and self.name(t_rel,n.func) in SESSION_TYPES for n in ast.walk(t_fn))
            if any(c["kind"]=="role_check" for c in item["checks"]): item["kind"]="role_check"
            elif kinds & ({"security_scheme"}|USER_KINDS): item["kind"]="authenticated_user"
            elif session and not item["requires"]: item["kind"]="database_session"
        else:
            item["unresolved"]="Dependency implementation not resolved"
            op["access"]["unresolved"].append("Dependency implementation not resolved: "+name)
            self.issue("unresolved_dependency",owner,call,"Dependency implementation is unresolved: "+name)
        op["evidence_ids"]+=item["evidence_ids"]
        return item

    def route(self, rel, fn, decorator, method, prefix, inherited, graph_evidence, registration="static"):
        route_node=decorator.args[0] if decorator.args else kw(decorator,"path")
        route_path=self.literal(rel,route_node)
        if not isinstance(route_path,str) or not route_path.startswith("/"): route_path=None
        path,settings_used,missing=None,[],[]
        if prefix is not None and route_path is not None:
            path,settings_used,missing=self.resolve_parts(prefix)
            if path is not None: path+=route_path
        template="".join(p if isinstance(p,str) else "<"+p["setting"]+">" for p in prefix)+route_path if prefix is not None and route_path is not None else None
        symbol=fn.name
        evidence=[self.evidence_for(rel,fn,symbol),*graph_evidence]+[e for s in settings_used for e in s["evidence_ids"]]
        discovery=dict(route=dict(status="resolved",registration=registration,template=template,settings=settings_used,runtime_verified=False,unresolved=[]),bindings=dict(status="resolved",unresolved=[]),schemas=dict(status="resolved",unknown_responses=[],unresolved=[]))
        access=dict(status="static_observations_only",runtime_verified=False,database_session=False,authentication="none_observed",role_checks=[],ownership_checks=[],branches=[],unresolved=[])
        op=dict(id=str(uuid.uuid5(uuid.NAMESPACE_URL,f"{self.business_id}:code:{rel}:{symbol}:{method}:{path}")),source_kind="code",source_pointer=f"{rel}:{fn.lineno}",method=method,path=path,summary=symbol,description="Declared function "+symbol,inputs={},responses={},parameters=[],request_body=None,security=[],access_status="unverified",supported=True,unresolved=[],dependencies=[],evidence_ids=evidence,discovery=discovery,access=access)
        def fail(area, reason):
            op["unresolved"].append(reason);discovery[area]["unresolved"].append(reason);discovery[area]["status"]="unresolved"
        if registration=="conditional": fail("route","Router is registered only under a runtime condition")
        elif prefix is None or route_path is None: fail("route","Route path or router prefix is dynamic/unregistered")
        for part in missing:
            default=f" (code default {part['code_default']!r} is not proof of the runtime value)" if part["code_default"] is not None else ""
            fail("route",f"Router prefix depends on setting {part['setting']}{default}; confirm the deployed value to resolve it")
        scope=template or route_path or ""
        if any(k.arg not in {"path","methods","response_model","status_code","dependencies","summary","description","tags","name","operation_id","deprecated","include_in_schema"} for k in decorator.keywords): fail("bindings","Unsupported route option")
        defaults=[None]*(len(fn.args.args)-len(fn.args.defaults))+list(fn.args.defaults)
        params=list(zip(fn.args.args,defaults))+list(zip(fn.args.kwonlyargs,fn.args.kw_defaults))
        body_schema=None
        deps=list(inherited)
        declared=kw(decorator,"dependencies")
        if declared is not None:
            if isinstance(declared,(ast.List,ast.Tuple)): deps += [(rel,d,None) for d in declared.elts]
            else: fail("bindings","Dynamic dependencies")
        users=set()
        for arg,default in params:
            dep=self.param_dependency(rel,arg,default)
            if dep is not None:
                if self.describe_dependency(*dep,op,"route parameter "+arg.arg)["kind"] in USER_KINDS: users.add(arg.arg)
                continue
            try:
                schema,ev=self.schema(rel,arg.annotation);op["evidence_ids"]+=ev
                if SECRET_NAME.search(arg.arg): schema={**schema,"x-sensitive":"credential"}
                marker=default
                annotation=arg.annotation
                if isinstance(annotation,ast.Subscript) and self.name(rel,annotation.value).split(".")[-1]=="Annotated" and isinstance(annotation.slice,ast.Tuple):
                    marker=next((n for n in annotation.slice.elts[1:] if isinstance(n,ast.Call)),default)
                marker_name=self.name(rel,marker.func) if isinstance(marker,ast.Call) else ""
                location="path" if "{"+arg.arg+"}" in scope else "query"
                needed=default is None
                if default is not None and not isinstance(default,ast.Call) and not schema.get("x-sensitive"):
                    ok,value=scalar_default(default)
                    if ok: schema["default"]=value
                if isinstance(marker,ast.Call):
                    if marker_name not in {"fastapi.Query","fastapi.Path","fastapi.Body","fastapi.Header","fastapi.Cookie"}: raise ValueError("Unknown parameter marker")
                    location=marker_name.split(".")[-1].lower()
                    if kw(marker,"alias") is not None or any(k.arg not in {"default","description","min_length","max_length","ge","le"} for k in marker.keywords): raise ValueError("Unsupported parameter option")
                    chosen=marker.args[0] if marker.args else kw(marker,"default")
                    needed=chosen is None or (isinstance(chosen,ast.Constant) and chosen.value is Ellipsis)
                    for source,target in {"min_length":"minLength","max_length":"maxLength","ge":"minimum","le":"maximum"}.items():
                        value=kw(marker,source)
                        if value is not None:
                            value=self.literal(rel,value)
                            if value is None: raise ValueError("Dynamic parameter constraint")
                            schema[target]=value
                if schema.get("type")=="object" or location=="body":
                    if body_schema is not None: raise ValueError("Multiple body parameters are unsupported")
                    body_schema=schema
                    op["request_body"]=dict(required=needed,content={"application/json":{"schema":schema}})
                    if schema.get("type")=="object":
                        for name,child in schema.get("properties",{}).items(): op["inputs"]["body."+name]=dict(schema=child,required=needed and name in schema.get("required",[]),description="")
                    else: op["inputs"]["body"]=dict(schema=schema,required=needed,description="")
                else:
                    op["inputs"][location+"."+arg.arg]=dict(schema=schema,required=needed or location=="path",description="")
            except ValueError as exc: fail("bindings",arg.arg+": "+str(exc))
        for owner,dep,base in deps:
            self.describe_dependency(owner,dep,base,op,"route declaration")
        if any(d["kind"]=="credential_form" for d in op["dependencies"]): fail("bindings","Credential form dependency inputs (OAuth2PasswordRequestForm) are not extracted")
        if fn.args.vararg or fn.args.kwarg: fail("bindings","Variadic route signature")
        for parameter in re.findall(r"\{([^}]+)\}",scope):
            if ":" in parameter: fail("bindings","Path converters require manual discovery")
            elif "path."+parameter not in op["inputs"]: fail("bindings","Path input is not declared in the route signature: "+parameter)
        response=kw(decorator,"response_model") or fn.returns
        status_node=kw(decorator,"status_code")
        status=self.literal(rel,status_node) if status_node else 200
        if not isinstance(status,int) or not 200<=status<=299: fail("schemas","response: Success status is unresolved")
        elif response is not None and self.name(rel,response) in {"typing.Any","typing_extensions.Any"}:
            # Unknown stays unknown: grounding rejects outputs/previous-step bindings that need this structure.
            op["responses"][str(status)]=dict(schema=None,description="Response structure unknown (declared Any)",headers={},content={})
            discovery["schemas"].update(status="partial",unknown_responses=[str(status)])
        else:
            try:
                schema,ev=self.schema(rel,response);op["evidence_ids"]+=ev
                op["responses"][str(status)]=dict(schema=schema,description="Declared response model",headers={},content={"application/json":{"schema":schema}})
            except ValueError as exc: fail("schemas","response: "+str(exc))
        self.assess(op,rel,fn,route_path,users)
        # Finite call tracing provides evidence, never creates callable operations.
        frontier=[(rel,fn)];visited=set()
        for _ in range(self.settings.code_exploration_steps):
            next_frontier=[]
            for owner,node in frontier:
                for call in (n for n in ast.walk(node) if isinstance(n,ast.Call)):
                    target=self.lookup(owner,call.func)
                    if target and isinstance(target[2],(ast.FunctionDef,ast.AsyncFunctionDef)) and (target[0],target[1]) not in visited:
                        visited.add((target[0],target[1]));op["evidence_ids"].append(self.evidence_for(target[0],target[2],target[1]));next_frontier.append((target[0],target[2]))
                        if len(visited)>=12: break
                if len(visited)>=12: break
            frontier=next_frontier[:12]
        op["call_trace"]=[dict(file=f,symbol=s) for f,s in sorted(visited)]
        op["evidence_ids"]=sorted(set(op["evidence_ids"]))
        op["supported"]=not op["unresolved"] and not op["exposure"]["restrictions"]
        for reason in op["unresolved"]: self.issue("unresolved_operation",rel,fn,reason)
        self.operations.append(op)

    def assess(self, op, rel, fn, route_path, users):
        """Observed access requirements and customer-facing suitability. Discoverable is not approved."""
        access,kinds=op["access"],{d["kind"] for d in op["dependencies"]}
        body=self.checks(rel,fn,users,False)
        access["database_session"]="database_session" in kinds
        access["authentication"]="observed_required" if kinds & USER_KINDS else "unknown" if access["unresolved"] else "none_observed"
        access["role_checks"]=[dict(c,source=d["symbol"]) for d in op["dependencies"] if d["kind"]=="role_check" for c in d["checks"] if c["kind"]=="role_check"]+[dict(c,source="handler") for c in body if c["kind"]=="role_check"]
        access["ownership_checks"]=[dict(c,source="handler") for c in body if c["kind"]=="ownership_check"]
        access["branches"]=[dict(c,source="handler") for c in body if c["kind"]=="conditional_branch"]
        restrictions,notes=[],[]
        if any(sensitive(f["schema"]) for f in op["inputs"].values()) or any(sensitive(r["schema"]) for r in op["responses"].values()) or "credential_form" in kinds:
            restrictions.append("Credential-bearing inputs or response fields are sensitive; not exposed to customer-facing tools")
        if AUTH_LIFECYCLE.search(op["summary"]) or AUTH_LIFECYCLE.search(route_path or ""):
            restrictions.append("Authentication/password lifecycle operation; not exposed to customer-facing tools")
        if access["role_checks"]:
            restrictions.append("Access depends on a role check ("+"; ".join(sorted({c["source"]+": "+c["condition"] for c in access["role_checks"]}))+"); role-dependent or admin operations are not exposed automatically")
        if access["unresolved"]:
            restrictions.append("Access requirements are partly unresolved")
        for check in access["ownership_checks"]:
            if check["user_attributes"]: notes.append(f"User attribute(s) {', '.join(check['user_attributes'])} bypass the ownership check at line {check['line']}")
        if access["branches"]:
            notes.append("Handler behavior branches on the authenticated user ("+"; ".join(c["condition"] for c in access["branches"])+"); effects are not verified")
        if op["discovery"]["schemas"]["unknown_responses"]:
            notes.append("Response structure is unknown; it cannot supply proposal outputs or later-step inputs")
        notes.append("Static observations do not verify runtime authentication, authorization or ownership; proposals still require owner review")
        status="restricted" if restrictions else "not_evaluable" if op["unresolved"] else "eligible_for_proposal"
        op["exposure"]=dict(status=status,restrictions=restrictions,notes=notes)

    def run(self):
        seen=set();expansions=0
        def expand(rel, receiver, prefix=(), dependencies=(), evidence=(), stack=(), registration="static"):
            nonlocal expansions
            expansions+=1
            if expansions>self.settings.max_operations*4:
                if expansions==self.settings.max_operations*4+1:self.issue("router_exploration_limit",rel)
                return
            key=(rel,receiver)
            if key in stack: self.issue("router_cycle",rel);return
            router=self.units[rel]["routers"][receiver]
            own=router["prefix"]
            full=[*prefix,*own] if prefix is not None and own is not None else None
            seen.add(key)
            evidence=(*evidence,self.evidence_for(rel,router["node"],receiver))
            declarations=kw(router["node"],"dependencies")
            deps=tuple(dependencies)
            if declarations is not None:
                if isinstance(declarations,(ast.List,ast.Tuple)): deps+=tuple((rel,d,None) for d in declarations.elts)
                else: self.issue("dynamic_router_dependencies",rel,router["node"]);full=None
            for fn in self.units[rel]["tree"].body:
                if not isinstance(fn,(ast.FunctionDef,ast.AsyncFunctionDef)): continue
                for dec in fn.decorator_list:
                    if isinstance(dec,ast.Call) and isinstance(dec.func,ast.Attribute) and dotted(dec.func.value)==receiver:
                        methods=[dec.func.attr] if dec.func.attr in METHODS else self.literal(rel,kw(dec,"methods")) if dec.func.attr=="api_route" else None
                        if not isinstance(methods,(list,tuple)) or any(not isinstance(m,str) or m.lower() not in METHODS for m in methods): self.issue("unsupported_route_registration",rel,dec);continue
                        for method in methods:
                            if len(self.operations)>=self.settings.max_operations: self.issue("operation_limit",rel,dec);return
                            self.route(rel,fn,dec,method.upper(),full,deps,evidence,registration)
            for call in self.units[rel]["includes"]:
                if dotted(call.func.value)!=receiver:continue
                target=self.resolve_router(rel,call.args[0] if call.args else kw(call,"router"))
                if not target: self.issue("unresolved_include_router",rel,call);continue
                included=kw(call,"prefix"); extra=self.parts(rel,included) if included is not None else []
                prefix_value=[*full,*extra] if full is not None and extra is not None else None
                inherit=list(deps);declared=kw(call,"dependencies")
                if declared is not None:
                    if isinstance(declared,(ast.List,ast.Tuple)): inherit += [(rel,d,None) for d in declared.elts]
                    else: prefix_value=None;self.issue("dynamic_include_dependencies",rel,call)
                if len(stack)>=8:self.issue("router_exploration_limit",rel,call);continue
                expand(target[0],target[1],prefix_value,inherit,(*evidence,self.evidence_for(rel,call,"include_router")),(*stack,key),registration)
        conditional=set()
        for rel,unit in self.units.items():
            for call in unit["conditional"]:
                target=self.resolve_router(rel,call.args[0] if call.args else kw(call,"router"))
                if target: conditional.add(target)
        for rel,unit in sorted(self.units.items()):
            for name,router in unit["routers"].items():
                if router["kind"]=="FastAPI": expand(rel,name)
        for rel,unit in sorted(self.units.items()):
            for name in unit["routers"]:
                if (rel,name) not in seen: expand(rel,name,None,registration="conditional" if (rel,name) in conditional else "unregistered")
            for node in ast.walk(unit["tree"]):
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr in {"add_api_route","mount"}: self.issue("unsupported_dynamic_registration",rel,node)
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=="include_router" and node not in unit["includes"]:self.issue("dynamic_include_router",rel,node)
                if isinstance(node,ast.Call) and self.name(rel,node.func) in {"fastapi.FastAPI","fastapi.APIRouter"} and not any(node is r["node"] for r in unit["routers"].values()): self.issue("unsupported_app_factory",rel,node)
        grouped={}
        for op in self.operations:
            if op["path"]: grouped.setdefault((op["method"],op["path"]),[]).append(op)
        for group in grouped.values():
            if len(group)>1:
                for op in group:
                    op["supported"]=False;op["unresolved"].append("Multiple declarations of method/path")
                    op["discovery"]["route"].update(status="unresolved",unresolved=[*op["discovery"]["route"]["unresolved"],"Multiple declarations of method/path"])
                    if op["exposure"]["status"]=="eligible_for_proposal": op["exposure"]["status"]="not_evaluable"
                self.issue("ambiguous_route",group[0]["source_pointer"])
        for name in sorted(set(self.setting_values)-self.confirmed_used):
            self.issue("unused_setting_confirmation",".",None,f"Confirmed setting {name} is not referenced by a statically identified route prefix")
        route_settings=[dict({k:v for k,v in s.items() if k!="evidence_ids"},confirmed_value=self.setting_values.get(s["setting"]),value_source="owner_confirmed" if s["setting"] in self.setting_values else "unconfirmed",runtime_verified=False) for s in self.settings_seen.values()]
        inventory=dict(source_kind="code",parser_version=VERSION,snapshot_id=self.indexed["snapshot"],valid=any(o["supported"] for o in self.operations),coverage="partial" if any(d["severity"]!="info" for d in self.diagnostics) else "supported subset",operations=self.operations,diagnostics=self.diagnostics,evidence=self.evidence,source_hashes={r:d["sha256"] for r,d in self.indexed["files"].items()},counts=self.indexed["counts"],interpretations=[],route_settings=route_settings,setting_confirmations=dict(self.setting_values))
        if not self.operations: self.issue("no_routes", "."); inventory["diagnostics"]=self.diagnostics
        inventory["discovery_summary"]=summarize(self.operations)
        inventory["limitations"]=["Only statically resolvable registrations and declared schemas are eligible. Internal functions are evidence only.","Dependencies are classified from static source (database session, authenticated user, role and ownership checks). This is not verified runtime authentication or authorization.","Settings-dependent prefixes resolve only from explicit owner-confirmed values. Code defaults are not proof of deployed values, and effective runtime paths remain unverified.","Credential-bearing, authentication-lifecycle and role-dependent operations are discoverable but restricted from customer-facing proposals.","Factory-created apps, conditional or dynamic registration, model validators and serialization options need manual discovery."]
        if any(e["snippet_truncated"] for e in self.evidence.values()):
            self.issue("snippet_limit", ".");inventory["coverage"]="partial"
        return inventory


def summarize(operations):
    count=lambda test:sum(1 for o in operations if test(o))
    reasons,restrictions={}, {}
    for o in operations:
        for r in o["unresolved"]: reasons[r]=reasons.get(r,0)+1
        for r in o["exposure"]["restrictions"]: restrictions[r]=restrictions.get(r,0)+1
    return dict(detected_routes=len(operations),route_resolved=count(lambda o:o["discovery"]["route"]["status"]=="resolved"),bindings_resolved=count(lambda o:o["discovery"]["bindings"]["status"]=="resolved"),schemas_resolved=count(lambda o:o["discovery"]["schemas"]["status"]!="unresolved"),fully_resolved=count(lambda o:not o["unresolved"]),restricted=count(lambda o:o["exposure"]["status"]=="restricted"),eligible_for_proposal=count(lambda o:o["supported"]),unresolved_reasons=dict(sorted(reasons.items(),key=lambda x:-x[1])),restriction_reasons=dict(sorted(restrictions.items(),key=lambda x:-x[1])))


def discover_project(indexed, business_id, settings, setting_values=None):
    return Inspector(indexed,business_id,settings,setting_values).run()


def validate_code_provenance(op, inventory):
    evidence=inventory.get("evidence",{})
    if not op.get("supported") or not op.get("path") or not op.get("evidence_ids"):
        raise ValueError("Operation is unresolved or lacks code provenance")
    for eid in op["evidence_ids"]:
        entry=evidence.get(eid)
        if not entry or entry.get("snapshot_id")!=inventory.get("snapshot_id") or entry.get("source_hash")!=inventory.get("source_hashes",{}).get(entry.get("file")):
            raise ValueError("Code evidence does not match the source snapshot")
        rel=Path(entry["file"])
        if rel.is_absolute() or ".." in rel.parts or not 1<=entry["line_start"]<=entry["line_end"]:
            raise ValueError("Invalid code evidence location")
        calculated=digest({k:entry[k] for k in ("file","symbol","line_start","line_end","source_hash","snapshot_id")})
        if calculated!=eid: raise ValueError("Code evidence identifier is invalid")
