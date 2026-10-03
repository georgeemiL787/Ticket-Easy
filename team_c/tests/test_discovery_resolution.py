"""Deterministic regression tests for grammar compatibility, settings prefixes, dependency
classification, schema handling and exposure. Model responses here are labeled substitutes."""
import ast
import hashlib
import json
import textwrap
import httpx
import pytest
from team_c.config import Settings, AppError
from team_c.code_discovery import index_project, discover_project, parse_source, validate_setting_values
from team_c.grounding import validate_proposal
from team_c.models import ProposalContent, GenerationOutput

FILES = {
    "app/__init__.py": "",
    "app/core/__init__.py": "",
    "app/core/config.py": """
        from pydantic_settings import BaseSettings

        class Settings(BaseSettings):
            API_PREFIX: str = "/api/v9"
            NO_DEFAULT: str
            FLAG: bool = False

        settings = Settings()
    """,
    "app/models.py": """
        import uuid
        from pydantic import EmailStr
        from sqlmodel import Field, SQLModel

        class User(SQLModel):
            id: uuid.UUID
            is_active: bool = True
            is_superuser: bool = False

        class Note(SQLModel):
            title: str = Field(min_length=1, max_length=50)
            body: str | None = Field(default=None, max_length=500)
            contact: EmailStr | None = None
            pinned: bool = False

        class NotePublic(Note):
            id: uuid.UUID
            owner_id: uuid.UUID

        class Login(SQLModel):
            email: EmailStr
            password: str = Field(min_length=8)
    """,
    "app/deps.py": """
        from typing import Annotated
        from fastapi import Depends, HTTPException
        from fastapi.security import OAuth2PasswordBearer
        from sqlmodel import Session
        from app.models import User

        bearer = OAuth2PasswordBearer(tokenUrl="token")

        def get_db():
            with Session(None) as session:
                yield session

        SessionDep = Annotated[Session, Depends(get_db)]
        TokenDep = Annotated[str, Depends(bearer)]

        def current_user(session: SessionDep, token: TokenDep) -> User:
            try:
                user = session.get(User, token)
            except KeyError, ValueError:
                raise HTTPException(status_code=403, detail="bad token")
            if not user.is_active:
                raise HTTPException(status_code=400, detail="inactive")
            return user

        CurrentUser = Annotated[User, Depends(current_user)]
        Me = CurrentUser

        def admin(user: CurrentUser) -> User:
            if not user.is_superuser:
                raise HTTPException(status_code=403, detail="no")
            return user
    """,
    "app/routes/__init__.py": "",
    "app/routes/notes.py": """
        import uuid
        from typing import Any
        from fastapi import APIRouter, Depends, HTTPException
        from app import deps
        from app.deps import SessionDep, Me as Owner, admin
        from app.models import Note, NotePublic, Login

        SECTION = "/notes"
        router = APIRouter(prefix=SECTION)

        @router.get("/{id}", response_model=NotePublic)
        def read_note(session: SessionDep, user: Owner, id: uuid.UUID) -> Any:
            note = session.get(Note, id)
            if not note:
                raise HTTPException(status_code=404)
            if not user.is_superuser and note.owner_id != user.id:
                raise HTTPException(status_code=403)
            return note

        @router.post("/", response_model=NotePublic, status_code=201)
        def create_note(*, session: SessionDep, user: deps.CurrentUser, note_in: Note, limit: int = 10) -> Any:
            return note_in

        @router.get("/export/raw")
        def export_raw(user: Owner) -> Any:
            return {}

        @router.delete("/{id}", dependencies=[Depends(admin)])
        def purge_note(session: SessionDep, id: uuid.UUID) -> Note:
            return None

        @router.post("/login/check")
        def check_login(body: Login) -> NotePublic:
            return None
    """,
    "app/routes/debug.py": """
        from fastapi import APIRouter
        router = APIRouter(prefix="/debug")

        @router.get("/ping")
        def ping() -> bool:
            return True
    """,
    "app/main.py": """
        from fastapi import FastAPI, APIRouter
        from app.core.config import settings
        from app.routes import debug
        from app.routes.notes import router as notes_router

        VERSION_SEGMENT = "/v1"
        api = APIRouter(prefix=VERSION_SEGMENT)
        api.include_router(notes_router, prefix="/team")
        api_alias = api
        app = FastAPI()
        app.include_router(api_alias, prefix=settings.API_PREFIX + "/public")
        if settings.FLAG:
            app.include_router(debug.router)
    """,
}


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "projects"; target = root / "svc"
    for rel, text in FILES.items():
        path = target / rel; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip())
    return target, Settings(_env_file=None, allowed_project_directory=str(root))


def inspect(project, confirmed=None):
    target, settings = project
    return discover_project(index_project(str(target), settings), "b", settings, confirmed)


def op(inv, name):
    return next(o for o in inv["operations"] if o["summary"] == name)


def hash_tree(target):
    return {p.relative_to(target).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in target.rglob("*") if p.is_file()}


# --- Python 3.14 PEP 758 syntax -------------------------------------------------------------

def test_pep758_except_is_parenthesized_in_memory_with_exact_lines():
    source = "def f():\n    try:\n        pass\n    except KeyError, ValueError:  # note\n        return 1\n    return 2\n"
    tree, adapted = parse_source(source, "x.py")
    assert adapted == [4]
    handler = next(n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler))
    assert handler.lineno == 4 and isinstance(handler.type, ast.Tuple)
    assert [e.id for e in handler.type.elts] == ["KeyError", "ValueError"]
    assert sorted(n.lineno for n in ast.walk(tree) if isinstance(n, ast.Return)) == [5, 6]


def test_other_syntax_errors_are_not_rewritten():
    # Invalid in every Python version, including 3.14.
    with pytest.raises(SyntaxError):
        parse_source("try:\n    pass\nexcept KeyError, ValueError as exc:\n    pass\n", "x.py")
    with pytest.raises(SyntaxError):
        parse_source("def broken(:\n    pass\n", "x.py")


def test_project_with_pep758_parses_and_keeps_hashes_and_unparseable_files_reported(project):
    target, settings = project
    (target / "app/broken.py").write_text("def broken(:\n    pass\n")
    before = hash_tree(target)
    indexed = index_project(str(target), settings)
    assert "app/deps.py" in indexed["files"] and "app/broken.py" not in indexed["files"]
    adapted = [d for d in indexed["diagnostics"] if d["code"] == "python_grammar_adapted"]
    line = (target / "app/deps.py").read_text().splitlines().index("    except KeyError, ValueError:") + 1
    assert adapted and adapted[0]["pointer"] == f"app/deps.py:{line}" and adapted[0]["severity"] == "info"
    assert any(d["code"] == "python_parse_error" and d["pointer"] == "app/broken.py:1" for d in indexed["diagnostics"])
    assert indexed["files"]["app/deps.py"]["sha256"] == before["app/deps.py"]
    inv = discover_project(indexed, "b", settings)
    dep = next(d for d in op(inv, "read_note")["dependencies"] if d["symbol"] == "current_user")
    evidence = inv["evidence"][dep["evidence_ids"][0]]
    lines = (target / "app/deps.py").read_text().splitlines()
    assert lines[evidence["line_start"] - 1].startswith("def current_user(")
    assert hash_tree(target) == before


# --- Settings-dependent and nested prefixes -------------------------------------------------

def test_settings_prefix_stays_unresolved_without_confirmation(project):
    inv = inspect(project)
    read = op(inv, "read_note")
    assert read["path"] is None and not read["supported"]
    route = read["discovery"]["route"]
    assert route["status"] == "unresolved" and route["template"] == "<API_PREFIX>/public/v1/team/notes/{id}"
    assert route["settings"][0]["code_default"] == "/api/v9" and route["settings"][0]["value_source"] == "unconfirmed"
    assert any("API_PREFIX" in r and "not proof" in r for r in read["unresolved"])
    # Bindings and schemas are still resolved independently of the path.
    assert read["discovery"]["bindings"]["status"] == "resolved" and read["inputs"]["path.id"]["required"]
    assert inv["route_settings"][0]["runtime_verified"] is False
    assert not inv["valid"]


def test_confirmed_setting_resolves_nested_prefixes_and_records_source(project):
    inv = inspect(project, {"API_PREFIX": "/gateway"})
    read = op(inv, "read_note")
    assert read["path"] == "/gateway/public/v1/team/notes/{id}" and read["supported"]
    setting = read["discovery"]["route"]["settings"][0]
    assert setting["value_source"] == "owner_confirmed" and setting["matches_code_default"] is False
    assert read["discovery"]["route"]["runtime_verified"] is False
    files = {inv["evidence"][e]["symbol"] for e in read["evidence_ids"]}
    assert {"Settings.API_PREFIX", "settings", "api", "router"} <= files


def test_conditional_registration_stays_unresolved(project):
    inv = inspect(project, {"API_PREFIX": "/api/v9"})
    ping = op(inv, "ping")
    assert ping["path"] is None and not ping["supported"]
    assert ping["discovery"]["route"]["registration"] == "conditional"
    assert "Router is registered only under a runtime condition" in ping["unresolved"]


def test_setting_without_default_or_non_settings_attribute_is_not_guessed(project):
    target, _ = project
    main = target / "app/main.py"
    main.write_text(main.read_text().replace("settings.API_PREFIX", "settings.NO_DEFAULT"))
    inv = inspect(project)
    read = op(inv, "read_note")
    assert read["path"] is None and read["discovery"]["route"]["settings"][0]["code_default"] is None
    main.write_text(main.read_text().replace("settings.NO_DEFAULT", "Holder().PREFIX").replace("app = FastAPI()", "class Holder:\n    PREFIX = '/x'\napp = FastAPI()"))
    inv = inspect(project, {"PREFIX": "/x"})
    assert op(inv, "read_note")["path"] is None
    assert any(d["code"] == "unused_setting_confirmation" for d in inv["diagnostics"])


@pytest.mark.parametrize("values", [{"SECRET_KEY": "/x"}, {"DB_PASSWORD": "/x"}, {"API_PREFIX": "api"}, {"API_PREFIX": "/api/"}, {"API_PREFIX": "/a b"}, {"bad-name": "/x"}])
def test_confirmations_must_be_non_secret_path_prefixes(values):
    with pytest.raises(AppError) as exc:
        validate_setting_values(values)
    assert exc.value.code == "setting_confirmation"


# --- Dependencies and access observations ---------------------------------------------------

def test_dependency_aliases_are_classified_without_claiming_verification(project):
    inv = inspect(project, {"API_PREFIX": "/api/v9"})
    read = op(inv, "read_note")
    kinds = {d["symbol"]: d["kind"] for d in read["dependencies"]}
    assert kinds == {"get_db": "database_session", "current_user": "authenticated_user", "bearer": "security_scheme"}
    assert not {"query.session", "query.user"} & set(read["inputs"])
    user = next(d for d in read["dependencies"] if d["kind"] == "authenticated_user")
    assert {(c["condition"], c["status"]) for c in user["checks"]} >= {("except (KeyError, ValueError)", 403), ("not user.is_active", 400)}
    access = read["access"]
    assert access["authentication"] == "observed_required" and access["runtime_verified"] is False and access["database_session"]
    assert access["ownership_checks"][0]["user_attributes"] == ["is_superuser"] and not access["role_checks"]
    assert read["exposure"]["status"] == "eligible_for_proposal"
    # Attribute alias (deps.CurrentUser) resolves the same way.
    assert "current_user" in {d["symbol"] for d in op(inv, "create_note")["dependencies"]}


def test_role_dependency_is_restricted(project):
    purge = op(inspect(project, {"API_PREFIX": "/api/v9"}), "purge_note")
    assert any(d["symbol"] == "admin" and d["kind"] == "role_check" for d in purge["dependencies"])
    assert purge["exposure"]["status"] == "restricted" and not purge["supported"] and not purge["unresolved"]
    assert any("role check" in r for r in purge["exposure"]["restrictions"])


def test_unresolved_dependencies_stay_unresolved(project):
    target, _ = project
    notes = target / "app/routes/notes.py"
    notes.write_text(notes.read_text().replace("def export_raw(user: Owner)", "def export_raw(user: Annotated[Unknown, Depends()], other=Depends(missing_dep))").replace("from typing import Any", "from typing import Annotated, Any"))
    raw = op(inspect(project, {"API_PREFIX": "/api/v9"}), "export_raw")
    assert raw["access"]["authentication"] == "unknown" and len(raw["access"]["unresolved"]) == 2
    assert raw["exposure"]["status"] == "restricted" and not raw["supported"]
    assert all(d.get("unresolved") for d in raw["dependencies"])


# --- Schemas ---------------------------------------------------------------------------------

def test_email_defaults_nullability_constraints_and_required(project):
    create = op(inspect(project, {"API_PREFIX": "/api/v9"}), "create_note")
    inputs = create["inputs"]
    assert inputs["body.title"] == dict(schema={"type": "string", "minLength": 1, "maxLength": 50}, required=True, description="")
    assert inputs["body.body"]["schema"] == {"type": "string", "nullable": True, "maxLength": 500, "default": None} and not inputs["body.body"]["required"]
    assert inputs["body.contact"]["schema"] == {"type": "string", "format": "email", "x-python-type": "pydantic.EmailStr", "nullable": True, "default": None}
    assert inputs["body.pinned"]["schema"]["default"] is False and not inputs["body.pinned"]["required"]
    assert inputs["query.limit"] == dict(schema={"type": "integer", "default": 10}, required=False, description="")
    assert create["responses"]["201"]["schema"]["required"] == ["id", "owner_id", "title"]
    assert create["supported"]


def test_credential_fields_are_extracted_as_sensitive_and_restricted(project):
    login = op(inspect(project, {"API_PREFIX": "/api/v9"}), "check_login")
    password = login["inputs"]["body.password"]["schema"]
    assert password["x-sensitive"] == "credential" and "default" not in password and password["minLength"] == 8
    assert login["inputs"]["body.email"]["schema"]["format"] == "email"
    assert not login["unresolved"] and not login["supported"] and login["exposure"]["status"] == "restricted"
    reasons = " ".join(login["exposure"]["restrictions"])
    assert "Credential-bearing" in reasons and "Authentication/password lifecycle" in reasons


def test_any_response_stays_unknown_and_blocks_only_output_use(project):
    inv = inspect(project, {"API_PREFIX": "/api/v9"})
    raw = op(inv, "export_raw")
    assert raw["responses"]["200"]["schema"] is None and raw["discovery"]["schemas"]["status"] == "partial"
    assert raw["supported"] and any("unknown" in n for n in raw["exposure"]["notes"])
    bad = proposal(read_id=raw["id"], output_step="s1", output_pointer="", bind_id=False)
    with pytest.raises(AppError) as exc:
        validate_proposal(ProposalContent(**bad), inv)
    assert any("no JSON schema" in e for e in exc.value.details["errors"])


def test_unknown_types_remain_unresolved(project):
    target, _ = project
    notes = target / "app/routes/notes.py"
    notes.write_text(notes.read_text().replace("note_in: Note,", "note_in: Mystery,"))
    create = op(inspect(project, {"API_PREFIX": "/api/v9"}), "create_note")
    assert not create["supported"] and create["discovery"]["bindings"]["status"] == "unresolved"
    assert create["exposure"]["status"] == "not_evaluable"


# --- Grounding and proposal workflow -------------------------------------------------------

def binding(target, kind, reference, step_id=None, response_status=None):
    return dict(target=target, kind=kind, reference=reference, step_id=step_id, response_status=response_status)


def proposal(read_id, output_step="s1", output_pointer="/title", bind_id=True):
    return dict(name="Note lookup", description="Look up a note", business_purpose="Help owners review notes", configuration=[], questions=[dict(id="q1", text="How will ownership be verified before use?", configuration_key=None)], steps=[dict(id="s1", operation_id=read_id, purpose="Read note", bindings=[binding("path.id", "runtime_argument", "note_id")] if bind_id else [])], outputs=[dict(name="note", step_id=output_step, response_status="200", pointer=output_pointer)], expected_reads=["Note"], expected_writes=[], assumptions=[], limitations=["Authorization unverified"], risk="medium", risk_rationale="Reads private records")


def test_eligible_operation_grounds_and_restricted_or_unresolved_do_not(project):
    inv = inspect(project, {"API_PREFIX": "/api/v9"})
    assert not validate_proposal(ProposalContent(**proposal(op(inv, "read_note")["id"])), inv)["blockers"]
    for name in ("purge_note",):
        with pytest.raises(AppError, match="grounding"):
            validate_proposal(ProposalContent(**proposal(op(inv, name)["id"])), inv)
    unconfirmed = inspect(project)
    with pytest.raises(AppError):
        validate_proposal(ProposalContent(**proposal(op(unconfirmed, "read_note")["id"])), unconfirmed)


def test_confirmations_change_cache_and_legacy_records_are_read_only(project, tmp_path):
    from team_c.storage import Store
    from team_c.service import Service
    target, settings = project; settings.database_path = str(tmp_path / "r.db")
    store = Store(settings.database_path); service = Service(settings, store, None)
    b = service.business("Notes", "Owner note tools")
    plain = service.local_project(b["id"], str(target))
    confirmed = service.local_project(b["id"], str(target), {"API_PREFIX": "/api/v9"})
    assert confirmed["id"] != plain["id"] and not confirmed["cache_hit"]
    assert service.local_project(b["id"], str(target), {"API_PREFIX": "/api/v9"})["cache_hit"]
    assert service.spec(confirmed["id"])["inventory"]["setting_confirmations"] == {"API_PREFIX": "/api/v9"}
    for spec in (plain, confirmed):
        with pytest.raises(AppError) as exc: service.generate(spec["id"])
        assert exc.value.code == "legacy_code_inventory"
    with pytest.raises(AppError): service.local_project(b["id"], str(target), {"SECRET_KEY": "/x"})


def test_model_facing_code_inventory_is_compact_but_stored_inventory_is_complete(project, tmp_path):
    from team_c.storage import Store
    from team_c.service import Service
    from team_c.providers import Providers
    target, settings = project; settings.database_path = str(tmp_path / "p.db")
    sent = []
    def handler(request):
        sent.append(json.loads(request.content)); return httpx.Response(500)
    store = Store(settings.database_path)
    service = Service(settings, store, Providers(settings, store, httpx.MockTransport(handler)))
    b = service.business("Notes", "Owner note tools")
    spec = service.local_project(b["id"], str(target), {"API_PREFIX": "/api/v9"})
    run = store.start_run(b["id"], "generation", {})
    with pytest.raises(AppError): service.providers.call("generation", dict(business=b, inventory=spec["inventory"]), GenerationOutput, run)
    data = json.loads(sent[0]["messages"][1]["content"].split("UNTRUSTED_DATA\n", 1)[1].rsplit("\nEND_UNTRUSTED_DATA", 1)[0])
    ops = data["inventory"]["operations"]
    assert {o["summary"] for o in ops} == {o["summary"] for o in spec["inventory"]["operations"] if o["supported"]}
    assert all("evidence_ids" not in o and all("content" not in r for r in o["responses"].values()) for o in ops)
    assert all(set(d) <= {"symbol", "kind", "scheme", "declared_scopes", "unresolved", "checks"} for o in ops for d in o["dependencies"])
    stored = service.spec(spec["id"])["inventory"]
    assert all(o["evidence_ids"] for o in stored["operations"])
