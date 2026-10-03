import hashlib
import json
import re
import sqlite3
import threading
from pathlib import Path
from urllib.parse import urljoin, urlparse
import httpx
from .config import AppError
from .discovery import discover
from .grounding import drop_stale_configuration, validate_proposal, response_only_fields
from .models import ProposalContent, GenerationOutput, ReconciliationOutput, CodeAnalysisOutput, RepairOutput, canonical_content
from .storage import INTERRUPTED, OWNER, uid, now, dump, digest
from .diagnostics import output_diagnostic, contract_diagnostic, check_errors
from . import requirements
from .artifacts import compile_artifact, validate_enforcement
from .executor import Run, credentials, destination, load_connectors, validate_arguments
from . import repair as repairs
from . import publishing
from . import capabilities
from . import areas as business_areas
from .providers import input_fits
from .models import RevisionSubmission, SandboxRunSubmission, RequestTriageOutput, SuggestionOutput, ToolRequestSubmission, AreaNamingOutput, AreaAssignmentOutput

CLOSED = {"approved_to_build", "rejected", "changes_requested", "superseded"}


def change_summary(old, new, labels, supersessions):
    """Readable differences between consecutive versions, shown before the owner decides."""
    lines = []
    def binding(b):
        if not b:
            return "not bound"
        return f'{b["kind"]} {b["reference"] or "(whole response)"}' + (f' from {b["step_id"]} {b["response_status"]}' if b.get("step_id") else "")
    for key in ("name", "description", "business_purpose", "risk", "risk_rationale"):
        if old[key] != new[key]:
            lines.append(f"{key}: {old[key]!r} -> {new[key]!r}")
    before, after = {s["id"]: s for s in old["steps"]}, {s["id"]: s for s in new["steps"]}
    for sid in sorted(set(before) | set(after)):
        a, b = before.get(sid), after.get(sid)
        if not a or not b:
            s = a or b
            lines.append(f'Step {sid} {"removed" if a else "added"}: {labels.get(s["operation_id"], s["operation_id"])}')
            continue
        if a["operation_id"] != b["operation_id"]:
            lines.append(f'Step {sid} operation: {labels.get(a["operation_id"])} -> {labels.get(b["operation_id"])}')
        if a["purpose"] != b["purpose"]:
            lines.append(f'Step {sid} purpose: {a["purpose"]!r} -> {b["purpose"]!r}')
        ab, bb = {x["target"]: x for x in a["bindings"]}, {x["target"]: x for x in b["bindings"]}
        for target in sorted(set(ab) | set(bb)):
            if ab.get(target) != bb.get(target):
                lines.append(f"Step {sid} input {target}: {binding(ab.get(target))} -> {binding(bb.get(target))}")
    for name, field, key, show in (("Configuration", "configuration", "key", lambda c: c["value_json"] or "unknown"), ("Output", "outputs", "name", lambda o: f'{o["step_id"]} {o["response_status"]} {o["pointer"] or "(whole response)"}')):
        a, b = {x[key]: x for x in old[field]}, {x[key]: x for x in new[field]}
        for k in sorted(set(a) | set(b)):
            if a.get(k) != b.get(k):
                lines.append(f"{name} {k}: {show(a[k]) if k in a else 'absent'} -> {show(b[k]) if k in b else 'removed'}")
    reasons = {s["question_id"]: s["reason"] for s in supersessions}
    a, b = {q["id"]: q for q in old["questions"]}, {q["id"]: q for q in new["questions"]}
    for qid in sorted(set(a) | set(b)):
        if qid not in b:
            lines.append(f'Question {qid} removed: {a[qid]["text"]!r}' + (f" (superseded: {reasons[qid]})" if qid in reasons else ""))
        elif qid not in a:
            lines.append(f'Question {qid} added: {b[qid]["text"]!r}')
        elif a[qid] != b[qid]:
            lines.append(f'Question {qid} changed: {a[qid]["text"]!r} -> {b[qid]["text"]!r}')
    for key in ("expected_reads", "expected_writes", "assumptions", "limitations"):
        lines += [f"{key} added: {x!r}" for x in new[key] if x not in old[key]]
        lines += [f"{key} removed: {x!r}" for x in old[key] if x not in new[key]]
    return lines


class Service:
    def __init__(self, settings, store, providers):
        self.settings, self.store, self.providers = settings, store, providers
        self.organizing = threading.Lock()
        self.fetch_transport = None
        self.execution_transport = None

    def business(self, name, description):
        if not name.strip() or not description.strip():
            raise AppError("business_required", "Business name and description are required")
        if len(name) > 200 or len(description) > 10000:
            raise AppError("business_limit", "Business name/description is too long")
        result = dict(id=uid(),name=name.strip(),description=description.strip(),created_at=now())
        with self.store.connect(write=True) as c:
            c.execute("INSERT INTO businesses VALUES(:id,:name,:description,:created_at)", result)
        return result

    def upload(self, business_id, filename, raw):
        self.store.one("SELECT * FROM businesses WHERE id=?", (business_id,))
        if len(raw) > self.settings.max_upload_bytes:
            raise AppError("upload_limit", "Upload exceeds 2 MiB", 413)
        if not filename.lower().endswith((".json", ".yaml", ".yml")):
            raise AppError("file_type", "Upload an OpenAPI JSON or YAML file")
        return self.save_openapi(business_id, filename, filename, raw, dict(kind="upload", filename=filename))

    def fetch(self, business_id, url):
        """GET an OpenAPI document from an allowlisted host; no redirects, credentials or proxies.
        A Swagger UI or ReDoc page (e.g. FastAPI /docs, /redoc) is followed to the OpenAPI URL it names: at most two GETs."""
        self.store.one("SELECT * FROM businesses WHERE id=?", (business_id,))
        allowed = {h.strip().lower() for h in self.settings.openapi_fetch_hosts.split(",") if h.strip()}
        if not allowed:
            raise AppError("fetch_disabled", "Set OPENAPI_FETCH_HOSTS (host:port list) to allow fetching OpenAPI descriptions", 403)
        url = self._fetch_url(url.strip(), allowed)
        raw, media = self._fetch_get(url)
        docs_page = None
        if media in {"text/html", "application/xhtml+xml"}:
            found = re.search(r"""(?:\burl\s*:|spec-url\s*=)\s*["']([^"']+)["']""", raw.decode("utf-8", "replace"))
            if not found:
                raise AppError("file_type", "The URL returned a web page, not an OpenAPI document, and no OpenAPI link was found in it. "
                                            "Enter the OpenAPI JSON or YAML URL instead (FastAPI serves it at /openapi.json).")
            docs_page, url = url, self._fetch_url(urljoin(url, found.group(1)), allowed)
            raw, media = self._fetch_get(url)
        path = urlparse(url).path.lower()
        generic = media in {"", "text/plain", "application/octet-stream"}
        if media in {"application/yaml", "application/x-yaml", "text/yaml", "application/vnd.oai.openapi"} or (generic and path.endswith((".yaml", ".yml"))):
            parse_as = "fetched.yaml"
        elif media == "application/json" or media.endswith("+json") or (generic and path.endswith(".json")):
            parse_as = "fetched.json"
        else:
            raise AppError("file_type", f"The URL returned {media or 'an unknown media type'}; expected OpenAPI JSON or YAML")
        source = dict(kind="url", url=url, fetched_at=now(), content_type=media, note="Observed from the running server at fetch time; other deployments may differ.")
        if docs_page:
            source["docs_page"] = docs_page
        return self.save_openapi(business_id, url, parse_as, bytes(raw), source)

    def _fetch_url(self, url, allowed):
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise AppError("fetch_url", "Enter an explicit http(s) URL without credentials or fragment")
        try:
            host = f"{parsed.hostname.lower()}:{parsed.port or (443 if parsed.scheme == 'https' else 80)}"
        except ValueError:
            raise AppError("fetch_url", "The URL port is invalid")
        if host not in allowed:
            raise AppError("fetch_host", f"{host} is not listed in OPENAPI_FETCH_HOSTS", 403)
        return url

    def _fetch_get(self, url):
        raw = bytearray()
        try:
            with httpx.Client(timeout=self.settings.openapi_fetch_timeout, follow_redirects=False, trust_env=False, transport=self.fetch_transport) as client:
                with client.stream("GET", url, headers={"Accept": "application/json, application/yaml;q=0.9, text/html;q=0.5"}) as response:
                    if response.status_code != 200:
                        hint = f" to {response.headers['location']}; enter that URL instead" if response.headers.get("location") else ""
                        raise AppError("fetch_status", f"{url} returned HTTP {response.status_code}{hint} (redirects are not followed)", 502)
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > self.settings.max_upload_bytes:
                            raise AppError("upload_limit", "Fetched document exceeds 2 MiB", 413)
                    media = response.headers.get("content-type", "").split(";")[0].strip().lower()
        except httpx.HTTPError as exc:
            raise AppError("fetch_failed", f"Could not fetch {url} ({type(exc).__name__}); is the server running?", 502)
        return raw, media

    def save_openapi(self, business_id, filename, parse_as, raw, source):
        doc, inventory = discover(raw, parse_as, business_id, self.settings.max_operations)
        spec_id = uid()
        checksum = hashlib.sha256(raw).hexdigest()
        inventory["source"] = dict(source, sha256=checksum, bytes=len(raw))
        with self.store.connect(write=True) as c:
            c.execute("INSERT INTO specifications VALUES(?,?,?,?,?,?,?,?)", (spec_id,business_id,filename,checksum,raw,dump(doc) if doc else None,dump(inventory),now()))
        return dict(id=spec_id, checksum=checksum, inventory=inventory)

    def spec(self, spec_id):
        row = self.store.one("SELECT id,business_id,filename,checksum,inventory FROM specifications WHERE id=?", (spec_id,))
        inventory = row["inventory"] = json.loads(row["inventory"])
        if "proposal_generation_ready" not in inventory:
            # Records stored before these fields existed; legacy code inventories are read-only.
            legacy = inventory.get("source_kind") == "code"
            eligible = sum(1 for o in inventory["operations"] if o.get("proposal_eligible", o.get("supported", False)))
            inventory.setdefault("document_valid", inventory["valid"] if legacy else not any(d.get("severity") == "error" and d.get("scope") == "document" for d in inventory.get("diagnostics", [])))
            inventory.update(eligible_operation_count=eligible, proposal_generation_ready=not legacy and inventory["document_valid"] and eligible > 0)
        return row

    def local_project(self, business_id, path, setting_values=None):
        from .code_discovery import index_project, discover_project, validate_setting_values
        self.store.one("SELECT * FROM businesses WHERE id=?", (business_id,))
        confirmed = validate_setting_values(setting_values)
        indexed = index_project(path, self.settings)
        key = digest(dict(kind="code_facts",business=business_id,root=indexed["root"],snapshot=indexed["snapshot"],confirmed_settings=confirmed))
        cached = self.store.all("SELECT spec_id FROM discovery_cache WHERE cache_key=?", (key,))
        if cached:
            return dict(self.spec(cached[0]["spec_id"]), cache_hit=True)
        inventory = discover_project(indexed,business_id,self.settings,confirmed)
        document = dict(source_kind="code",project_path=indexed["root"],snapshot_id=indexed["snapshot"],config=indexed["config"],confirmed_settings=confirmed)
        sid = self.save_code_spec(business_id, "Local project: "+Path(indexed["root"]).name, document, inventory, key)
        return dict(self.spec(sid),cache_hit=False)

    def save_code_spec(self, business_id, filename, document, inventory, cache_key):
        raw=dump(document).encode();checksum=digest(dict(document=document,inventory=inventory));sid=uid()
        with self.store.connect(write=True) as c:
            existing=c.execute("SELECT spec_id FROM discovery_cache WHERE cache_key=?",(cache_key,)).fetchone()
            if existing: return existing["spec_id"]
            c.execute("INSERT INTO specifications VALUES(?,?,?,?,?,?,?,?)",(sid,business_id,filename,checksum,raw,dump(document),dump(inventory),now()))
            c.execute("INSERT INTO discovery_cache VALUES(?,?)",(cache_key,sid))
        return sid

    def analyze_code(self, spec_id):
        from .providers import CODE_SYSTEM
        spec=self.spec(spec_id);inventory=spec["inventory"]
        if inventory.get("source_kind")=="code":
            raise AppError("legacy_code_inventory","Local-code inventories are retained read-only; code analysis is retired",409)
        if inventory.get("source_kind")!="code" or not inventory["valid"]:
            raise AppError("code_analysis_blocked","Code analysis requires at least one supported code operation")
        business=self.store.one("SELECT * FROM businesses WHERE id=?",(spec["business_id"],))
        self.providers.configured_chain() if hasattr(self.providers,"configured_chain") else None
        provider_config={k:getattr(self.settings,k) for k in ("llm_primary","llm_fallback","ollama_model","ollama_base_url","ollama_think","ollama_context","openrouter_model","openrouter_base_url")}
        key=digest(dict(kind="code_analysis",source=spec["checksum"],business=business["description"],prompt=CODE_SYSTEM,schema=CodeAnalysisOutput.model_json_schema(),provider_config=provider_config))
        cached=self.store.all("SELECT spec_id FROM discovery_cache WHERE cache_key=?",(key,))
        if cached: return dict(self.spec(cached[0]["spec_id"]),cache_hit=True)
        selected=[];selected_ids=set();chars=0;primary={};extras=[]
        for op in inventory["operations"]:
            if not op["supported"]:continue
            primary[op["id"]]=next(eid for eid in op["evidence_ids"] if inventory["evidence"][eid]["symbol"]==op["summary"] and f'{inventory["evidence"][eid]["file"]}:{inventory["evidence"][eid]["line_start"]}'==op["source_pointer"])
            extras.extend(op["evidence_ids"])
        for eid in [*primary.values(),*extras]:
            entry=inventory["evidence"][eid]
            if eid in selected_ids:continue
            if len(selected)>=self.settings.code_max_snippets or chars+len(dump(entry))>self.settings.code_max_context_chars:continue
            selected.append(entry);selected_ids.add(eid);chars+=len(dump(entry))
        eligible=[o for o in inventory["operations"] if o["id"] in primary and primary[o["id"]] in selected_ids]
        if not selected or not eligible: raise AppError("code_context_limit","No code evidence fits the configured context limit")
        payload=dict(business=business,inventory=dict(inventory,operations=eligible,interpretations=[]),selected_code=selected)
        run,result=self.model_run(spec["business_id"],"code_analysis",payload,CodeAnalysisOutput)
        try:
            ops={o["id"]:o for o in eligible};seen=set()
            if not result.interpretations: raise AppError("empty_code_analysis","Model returned no code interpretations")
            for item in result.interpretations:
                if item.operation_id not in ops or item.operation_id in seen or not set(item.evidence_ids)<=set(ops[item.operation_id]["evidence_ids"]) & selected_ids:
                    raise AppError("invalid_code_analysis","Interpretation references invented, duplicate or unrelated operation/evidence IDs")
                observed={(c["file"],c["symbol"]) for c in ops[item.operation_id].get("call_trace",[])}
                call_ids={eid for eid in selected_ids if (inventory["evidence"][eid]["file"],inventory["evidence"][eid]["symbol"]) in observed}
                if not set(item.call_trace)<=call_ids:
                    raise AppError("invalid_code_analysis","Model call trace cites an unobserved or unrelated internal function")
                seen.add(item.operation_id)
            updated=json.loads(dump(inventory))
            updated["interpretations"]=[i.model_dump() for i in result.interpretations]
            updated["analysis_run_id"]=run
            updated["analysis_coverage"]=dict(operations_reviewed=len(seen),supported_operations=sum(o["supported"] for o in inventory["operations"]),snippets=len(selected),chars=chars,partial=len(seen)<sum(o["supported"] for o in inventory["operations"]) or len(selected_ids)<len(inventory["evidence"]) or any(e.get("snippet_truncated") for e in selected))
            document=json.loads(self.store.one("SELECT document FROM specifications WHERE id=?",(spec_id,))["document"])
            document["facts_spec_id"]=spec_id
            sid=self.save_code_spec(spec["business_id"],spec["filename"]+" (AI explained)",document,updated,key)
            self.store.finish_run(run,dict(spec_id=sid,analysis_coverage=updated["analysis_coverage"]))
            return dict(self.spec(sid),cache_hit=False,run_id=run)
        except Exception as exc:
            self.fail_run(run,exc);raise

    def view(self, pid, version=None):
        proposal = self.store.one("SELECT * FROM proposals WHERE id=?", (pid,))
        version = version or proposal["current_version"]
        row = self.store.one("SELECT * FROM versions WHERE proposal_id=? AND version=?", (pid,version))
        for field in ("content", "derived"):
            row[field] = json.loads(row[field])
        row["business_id"] = proposal["business_id"]
        row["current_version"] = proposal["current_version"]
        row["answer_history"] = self.store.all("SELECT * FROM answers WHERE proposal_id=? AND version=? ORDER BY id", (pid,version))
        row["answers"] = {a["question_id"]: a for a in row["answer_history"]}
        row["decisions"] = self.store.all("SELECT * FROM decisions WHERE proposal_id=? ORDER BY created_at", (pid,))
        row["versions"] = self.store.all("SELECT version,state,created_at FROM versions WHERE proposal_id=? ORDER BY version", (pid,))
        row["reconciliations"] = self.store.all("SELECT * FROM reconciliations WHERE proposal_id=? AND version=? ORDER BY created_at", (pid,version))
        for rec in row["reconciliations"]:
            rec["result"] = json.loads(rec["result"])
        row["attempts"] = self.store.all("SELECT * FROM attempts WHERE run_id=? ORDER BY id", (row["run_id"],))
        inventory = self.spec(row["spec_id"])["inventory"]
        ops = {o["id"]: o for o in inventory["operations"]}
        with self.store.connect() as c:
            reqs = requirements.derive(row["content"]["steps"], inventory)
            row["requirements"] = requirements.current(c, pid, version, reqs, row["answers"], row["reconciliation_id"])
        row["supersessions"] = self.store.all("SELECT * FROM question_supersessions WHERE proposal_id=? ORDER BY created_at", (pid,))
        row["repairs"] = self.store.all("SELECT * FROM repairs WHERE proposal_id=? ORDER BY created_at", (pid,))
        row["enforcement"] = self.current_enforcement(pid, version)
        row["change_summary"] = []
        if version > 1:
            previous = json.loads(self.store.one("SELECT content FROM versions WHERE proposal_id=? AND version=?", (pid, version - 1))["content"])
            labels = {i: f'{o["method"]} {o["path"]}' for i, o in ops.items()}
            row["change_summary"] = change_summary(previous, row["content"], labels, [s for s in row["supersessions"] if s["new_version"] == version])
        row["question_review"] = response_only_fields(ProposalContent.model_validate(row["content"]), ops)
        row["operation_states"] = {s["operation_id"]: dict(technically_supported=ops[s["operation_id"]].get("supported", False), proposal_eligible=ops[s["operation_id"]].get("proposal_eligible", ops[s["operation_id"]].get("supported", False))) for s in row["content"]["steps"] if s["operation_id"] in ops}
        row["lifecycle"] = dict(approved_to_build=row["state"] == "approved_to_build", requirements_confirmed=all(r["status"] == "owner_confirmed" for r in row["requirements"]), runtime_ready=False, note=requirements.RUNTIME_NOTE)
        return row

    @staticmethod
    def snapshot(view, checksum):
        return dict(content=view["content"], answers=view["answers"], checksum=checksum, version=view["version"])

    @staticmethod
    def check_current(c, pid, version, revision, allow_closed=False):
        row = c.execute("SELECT v.*,p.current_version FROM versions v JOIN proposals p ON p.id=v.proposal_id WHERE v.proposal_id=? AND v.version=?", (pid,version)).fetchone()
        if not row:
            raise AppError("not_found", "Proposal version not found",404)
        if row["current_version"] != version or row["review_revision"] != revision:
            raise AppError("stale_review", "The proposal or answers changed. Refresh before continuing",409)
        if not allow_closed and row["state"] in CLOSED:
            raise AppError("closed_version", "This version has a decision; create a new version to change it",409)
        return dict(row)

    @staticmethod
    def settle(c, pid, version, content, fields):
        """Persist requirements; a version needs clarification until every question and requirement has an answer."""
        reqs = requirements.ensure(c, pid, version)
        latest = {a["question_id"]: a["text"] for a in c.execute("SELECT question_id,text FROM answers WHERE proposal_id=? AND version=? ORDER BY id", (pid, version))}
        missing = [i for i in [q.id for q in content.questions] + [r["id"] for r in reqs] if not latest.get(i)]
        state = "needs_clarification" if missing or fields["blockers"] else "needs_reconciliation"
        c.execute("UPDATE versions SET state=? WHERE proposal_id=? AND version=?", (state, pid, version))
        return state

    def model_run(self, business_id, kind, payload, output):
        run = self.store.start_run(business_id, kind, payload)
        try:
            result = self.providers.call(kind, payload, output, run)
            return run, result
        except Exception as exc:
            self.fail_run(run, exc)
            raise

    def fail_run(self, run, exc):
        error = dict(code=exc.code if isinstance(exc, AppError) else "internal_error",message=str(exc) if isinstance(exc, AppError) else "Unexpected processing failure")
        if isinstance(exc, AppError) and "configuration_contract" in exc.details:
            secrets = (self.settings.openrouter_api_key, self.settings.session_secret)
            error["details"] = {"configuration_contract": contract_diagnostic(exc.details["configuration_contract"], secrets), "errors": check_errors(exc.details.get("errors", []), secrets)}
            if "proposal_index" in exc.details:
                error["details"]["proposal_index"] = exc.details["proposal_index"]
            self.store.diagnostic(run, "grounding_error", error["details"])
        self.store.finish_run(run, error=error)
        if isinstance(exc, AppError):
            exc.details["run_id"] = run

    def tidy(self, run, content):
        content, stale = drop_stale_configuration(content)
        if stale:
            self.store.diagnostic(run, "stale_configuration_removed", dict(keys=stale))
        return content

    @staticmethod
    def scoped(inventory, scope):
        return inventory if scope is None else dict(inventory, operations=[o for o in inventory["operations"] if o["id"] in scope])

    def generate(self, spec_id, operation_ids=None, request=None):
        spec = self.spec(spec_id)
        if spec["inventory"].get("source_kind") == "code":
            raise AppError("legacy_code_inventory", "Local-code inventories are retained read-only; discover the system from its OpenAPI description to generate proposals", 409)
        if not spec["inventory"]["proposal_generation_ready"]:
            raise AppError("inventory_blocked", "No proposal-eligible operation; review discovery diagnostics")
        eligible = [o["id"] for o in spec["inventory"]["operations"] if o.get("proposal_eligible", o.get("supported", True))]
        scope = list(dict.fromkeys(operation_ids)) if operation_ids else eligible
        if not set(scope) <= set(eligible):
            raise AppError("operation_scope", "Selected operations must be proposal-eligible operations of this inventory")
        business = self.store.one("SELECT * FROM businesses WHERE id=?", (spec["business_id"],))
        payload = dict(business=business, inventory=self.scoped(spec["inventory"], scope), contract_version="1")
        if request:
            payload["owner_request"] = {k: request[k] for k in ("goal", "examples", "clarifications")}
        try:
            return self.generation_attempt(spec, scope, payload, request)
        except AppError as exc:
            rejected = exc.details.pop("proposal", None)
            if exc.code != "invalid_bindings" or rejected is None:
                raise
            earlier = exc.details["run_id"]
            # One automatic retry: the model sees its rejected proposal and exactly what the check found.
            payload = dict(payload, grounding_feedback=dict(previous_proposal=rejected, errors=check_errors(exc.details.get("errors", []), (self.settings.openrouter_api_key, self.settings.session_secret))))
        try:
            return dict(self.generation_attempt(spec, scope, payload, request), earlier_run_ids=[earlier])
        except AppError as exc:
            exc.details.pop("proposal", None)
            exc.details["earlier_run_ids"] = [earlier]
            raise

    def generation_attempt(self, spec, scope, payload, request):
        run, result = self.model_run(spec["business_id"], "generation", payload, GenerationOutput)
        with self.store.connect(write=True) as c:
            c.execute("INSERT INTO generation_batches VALUES(?,?,?,?)", (run, spec["id"], dump(scope), now()))
        try:
            if not result.proposals and not result.capability_gaps:
                raise AppError("empty_model_output", "Model returned neither proposals nor capability gaps")
            result.proposals = [self.tidy(run, canonical_content(p)) for p in result.proposals]
            self.store.diagnostic(run, "normalized_output", output_diagnostic(result.model_dump(), (self.settings.openrouter_api_key, self.settings.session_secret)))
            derived = []
            for index, p in enumerate(result.proposals):
                try:
                    derived.append(validate_proposal(p, spec["inventory"], scope))
                except AppError as exc:
                    exc.details["proposal_index"] = index
                    exc.details["proposal"] = p.model_dump()
                    raise
            for fields in derived:
                fields["generation_scope"] = scope
                fields["generation_capability_gaps"] = [g.model_dump() for g in result.capability_gaps]
                fields["blockers"].extend("Generation capability gap: " + g.explanation for g in result.capability_gaps)
            created, duplicates = list(zip(result.proposals, derived)), []
            if request:
                known = {dump(capabilities.signature(t["content"])): t["proposal_id"] for t in self.current_contents(spec["business_id"]) if t["state"] not in ("rejected", "superseded")}
                created = []
                for content, fields in zip(result.proposals, derived):
                    match = known.get(dump(capabilities.signature(content.model_dump())))
                    if match:
                        duplicates.append(match)
                    else:
                        created.append((content, dict(fields, owner_request_id=request["id"])))
            ids = []
            with self.store.connect(write=True) as c:
                for content, fields in created:
                    pid = uid()
                    ids.append(pid)
                    c.execute("INSERT INTO proposals VALUES(?,?,1)",(pid,spec["business_id"]))
                    c.execute("INSERT INTO versions(proposal_id,version,spec_id,content,derived,state,run_id,created_at) VALUES(?,1,?,?,?,'needs_clarification',?,?)",(pid,spec["id"],dump(content.model_dump()),dump(fields),run,now()))
                    self.settle(c,pid,1,content,fields)
                response = dict(run_id=run,proposal_ids=ids,capability_gaps=[g.model_dump() for g in result.capability_gaps],duplicates=duplicates)
                c.execute("UPDATE runs SET status='succeeded',result=?,completed_at=? WHERE id=?",(dump(response),now(),run))
            return response
        except Exception as exc:
            self.fail_run(run,exc)
            raise

    def answers(self, pid, version, submission):
        if any(len(text) > 10000 for text in submission.answers.values()):
            raise AppError("answer_limit", "An answer exceeds 10,000 characters")
        with self.store.connect(write=True) as c:
            row = self.check_current(c,pid,version,submission.expected_revision)
            known = {q["id"] for q in json.loads(row["content"])["questions"]} | {r["id"] for r in requirements.ensure(c,pid,version)}
            if not set(submission.answers) <= known:
                raise AppError("unknown_question", "An answer references an unknown question or requirement")
            for qid,text in submission.answers.items():
                c.execute("INSERT INTO answers(proposal_id,version,question_id,text,reviewer,created_at) VALUES(?,?,?,?,?,?)",(pid,version,qid,text.strip(),self.settings.dev_reviewer_id,now()))
            c.execute("UPDATE versions SET state='needs_reconciliation',reconciliation_id=NULL,review_revision=review_revision+1 WHERE proposal_id=? AND version=?",(pid,version))
        return self.view(pid,version)

    def reconcile(self, pid, version, revision):
        view = self.view(pid,version)
        with self.store.connect() as c:
            self.check_current(c,pid,version,revision)
        spec = self.spec(view["spec_id"])
        snapshot = self.snapshot(view,spec["checksum"])
        scope = view["derived"].get("generation_scope")
        reqs = view["requirements"]
        latest = {a["id"] for a in view["answers"].values()}
        payload = dict(proposal=snapshot,earlier_answers=[a for a in view["answer_history"] if a["id"] not in latest],requirements=[{k:r[k] for k in ("id","text","source_facts")} for r in reqs],inventory=self.scoped(spec["inventory"],scope),business=self.store.one("SELECT * FROM businesses WHERE id=?",(view["business_id"],)))
        run,result = self.model_run(view["business_id"],"reconciliation",payload,ReconciliationOutput)
        earlier = []
        rejected = self.rejected_revision(result,spec,scope)
        if rejected:
            # One automatic retry: the model sees its rejected revised proposal and exactly what the check found.
            self.fail_run(run,rejected)
            earlier = [run]
            payload = dict(payload,grounding_feedback=dict(previous_proposal=result.revised_proposal.model_dump(),errors=check_errors(rejected.details["errors"],(self.settings.openrouter_api_key,self.settings.session_secret))))
            run,result = self.model_run(view["business_id"],"reconciliation",payload,ReconciliationOutput)
        try:
            old = canonical_content(ProposalContent.model_validate(view["content"]))
            question_ids = {q.id for q in old.questions}
            expected = question_ids | {r["id"] for r in reqs}
            findings = {f.question_id: f for f in result.findings}
            if set(findings) != expected or len(findings) != len(result.findings):
                raise AppError("invalid_reconciliation", "Reconciliation must assess every question and access requirement exactly once")
            history = {a["id"]:a for a in view["answer_history"]}
            for qid,f in findings.items():
                answer = view["answers"].get(qid)
                if any(i not in history or history[i]["question_id"] != qid for i in f.answer_revision_ids):
                    raise AppError("invalid_reconciliation", "Finding cites nonexistent or unrelated answer evidence")
                if f.status == "resolved" and (not answer or not answer["text"].strip() or answer["id"] not in f.answer_revision_ids):
                    raise AppError("invalid_reconciliation", "Resolved findings must cite the latest nonempty answer")
                # Basic evasive responses cannot be laundered into resolution by a model.
                if f.status == "resolved" and answer["text"].strip().lower() in {"whatever", "idk", "don't know", "n/a", "?"}:
                    f.status, f.explanation = "insufficient", "Please provide an explicit answer to the design question."
            candidate = self.tidy(run, canonical_content(result.revised_proposal or old))
            if {q.id for q in candidate.questions} != question_ids:
                raise AppError("invalid_reconciliation", "Reconciliation cannot remove or add questions; supersede a question or use an explicit revision request")
            fields = validate_proposal(candidate,spec["inventory"],scope)
            if scope is not None:
                fields["generation_scope"] = scope
            candidate_configs = {c.key:c for c in candidate.configuration}
            for question in candidate.questions:
                if question.configuration_key and candidate_configs[question.configuration_key].value_json is None and findings[question.id].status == "resolved":
                    findings[question.id].status = "insufficient"
                    findings[question.id].explanation = "The answer has not established the required business configuration value. Provide its exact value; it is not a future customer input."
            # An original capability gap cannot disappear through an answer-only reconciliation.
            # An explicit validated revision must address or remove the requested unsupported scope.
            gaps = view["derived"].get("generation_capability_gaps", [])
            fields["generation_capability_gaps"] = gaps
            fields["blockers"].extend("Requires explicit revision: " + g["explanation"] for g in gaps)
            changed = digest(candidate.model_dump()) != digest(old.model_dump())
            all_resolved = all(f.status == "resolved" for f in findings.values()) and not result.capability_gaps and not fields["blockers"]
            with self.store.connect(write=True) as c:
                self.check_current(c,pid,version,revision)
                rec_id = uid()
                result_data = result.model_dump()
                result_data["findings"] = [f.model_dump() for f in findings.values()]
                result_data["validation_blockers"] = fields["blockers"]
                c.execute("INSERT INTO reconciliations VALUES(?,?,?,?,?,?,?,?)",(rec_id,pid,version,digest(snapshot),dump(result_data),int(all_resolved and not changed),run,now()))
                requirements.ensure(c,pid,version)
                for r in reqs:
                    f, answer = findings[r["id"]], view["answers"].get(r["id"])
                    # A sufficient assessment of an already confirmed answer keeps the confirmation.
                    if not (f.status == "resolved" and r["status"] == "owner_confirmed"):
                        requirements.event(c,pid,version,r["id"],"answer_sufficient" if f.status == "resolved" else f.status,"model",f.explanation,"model_assessment",answer["id"] if answer else None,rec_id)
                if changed:
                    new_version = version+1
                    c.execute("UPDATE versions SET state='superseded',review_revision=review_revision+1 WHERE proposal_id=? AND version=?",(pid,version))
                    c.execute("UPDATE proposals SET current_version=? WHERE id=?",(new_version,pid))
                    c.execute("INSERT INTO versions(proposal_id,version,spec_id,content,derived,state,run_id,created_at) VALUES(?,?,?,?,?,'needs_reconciliation',?,?)",(pid,new_version,view["spec_id"],dump(candidate.model_dump()),dump(fields),run,now()))
                    # Copy answers as new evidence, explicitly requiring another reconciliation.
                    for a in view["answers"].values():
                        c.execute("INSERT INTO answers(proposal_id,version,question_id,text,reviewer,created_at) VALUES(?,?,?,?,?,?)",(pid,new_version,a["question_id"],a["text"],a["reviewer"],now()))
                    requirements.ensure(c,pid,new_version)
                    response = dict(run_id=run,version=new_version,state="needs_reconciliation",material_change=True)
                else:
                    state = "ready_for_review" if all_resolved else "needs_clarification"
                    c.execute("UPDATE versions SET state=?,derived=?,reconciliation_id=?,review_revision=review_revision+1 WHERE proposal_id=? AND version=?",(state,dump(fields),rec_id,pid,version))
                    response = dict(run_id=run,version=version,state=state,material_change=False)
                c.execute("UPDATE runs SET status='succeeded',result=?,completed_at=? WHERE id=?",(dump(response),now(),run))
            return response
        except Exception as exc:
            self.fail_run(run,exc)
            if earlier and isinstance(exc, AppError):
                exc.details["earlier_run_ids"] = earlier
            raise

    def rejected_revision(self, result, spec, scope):
        """The grounding failure of a reconciliation's revised proposal, if any; reconcile checks and records it again."""
        if not result.revised_proposal:
            return None
        content, _ = drop_stale_configuration(canonical_content(result.revised_proposal))
        try:
            validate_proposal(content, spec["inventory"], scope)
        except AppError as exc:
            if exc.code == "invalid_bindings":
                return exc
        return None

    def decide(self,pid,version,submission):
        request_hash = digest(dict(pid=pid,version=version,action=submission.action,reason=submission.reason,reviewer=self.settings.dev_reviewer_id))
        with self.store.connect(write=True) as c:
            prior = c.execute("SELECT * FROM decisions WHERE idempotency_key=? OR (proposal_id=? AND version=?)",(submission.idempotency_key,pid,version)).fetchone()
            if prior:
                if prior["request_hash"] == request_hash:
                    return dict(prior)
                raise AppError("decision_conflict","A different decision already exists or this idempotency key was used",409)
            row = self.check_current(c,pid,version,submission.expected_revision)
            answers = {a["question_id"]:dict(a) for a in c.execute("SELECT * FROM answers WHERE proposal_id=? AND version=? ORDER BY id",(pid,version))}
            spec = dict(c.execute("SELECT checksum,inventory FROM specifications WHERE id=?",(row["spec_id"],)).fetchone())
            snapshot = dict(content=json.loads(row["content"]),answers=answers,checksum=spec["checksum"],version=version)
            if submission.action == "approve_to_build":
                rec = c.execute("SELECT * FROM reconciliations WHERE id=?",(row["reconciliation_id"],)).fetchone()
                if row["state"] != "ready_for_review" or not rec or not rec["successful"] or rec["snapshot_hash"] != digest(snapshot):
                    raise AppError("approval_blocked","Approval requires current successful reconciliation and validation")
                fields = validate_proposal(ProposalContent.model_validate(snapshot["content"]),json.loads(spec["inventory"]),json.loads(row["derived"]).get("generation_scope"))
                if fields["blockers"]:
                    raise AppError("approval_blocked","Unresolved configuration remains")
            reqs = requirements.current(c,pid,version,requirements.ensure(c,pid,version),answers,row["reconciliation_id"])
            pending = [r["id"] for r in reqs if r["status"] != "owner_confirmed"]
            if submission.action == "approve_to_build" and pending:
                raise AppError("approval_blocked","Every access/identity requirement needs a sufficient answer confirmed by the owner: "+", ".join(pending),details={"requirements":pending})
            if submission.action == "request_changes" and not submission.reason.strip():
                raise AppError("reason_required","Explain the requested changes")
            state = {"approve_to_build":"approved_to_build","reject":"rejected","request_changes":"changes_requested"}[submission.action]
            requirement_states = [dict(id=r["id"],status=r["status"],answer_id=r["answer"]["id"] if r["answer"] else None) for r in reqs]
            decision = dict(id=uid(),proposal_id=pid,version=version,action=submission.action,reason=submission.reason,reviewer=self.settings.dev_reviewer_id,idempotency_key=submission.idempotency_key,request_hash=request_hash,snapshot=dump(dict(**snapshot,reconciliation_id=row["reconciliation_id"],requirements=requirement_states,runtime_ready=False)),created_at=now())
            c.execute("INSERT INTO decisions VALUES(:id,:proposal_id,:version,:action,:reason,:reviewer,:idempotency_key,:request_hash,:snapshot,:created_at)",decision)
            c.execute("UPDATE versions SET state=?,review_revision=review_revision+1 WHERE proposal_id=? AND version=?",(state,pid,version))
        return decision

    def revise(self,pid,submission,constraint=None,actor=None,repair=False):
        view=self.view(pid)
        with self.store.connect() as c:
            self.check_current(c,pid,view["version"],submission.expected_revision,True)
        spec=self.spec(view["spec_id"])
        scope=view["derived"].get("generation_scope")
        payload=dict(proposal=view["content"],instruction=submission.instruction,answer_history=view["answer_history"],inventory=self.scoped(spec["inventory"],scope),business=self.store.one("SELECT * FROM businesses WHERE id=?",(view["business_id"],)))
        run=self.store.start_run(view["business_id"],"repair" if repair else "revision",payload)
        with self.store.connect(write=True) as c:
            c.execute("INSERT INTO revision_requests VALUES(?,?,?,?,?,?,?)",(uid(),pid,view["version"],submission.instruction,actor or self.settings.dev_reviewer_id,run,now()))
        try:
            if repair:
                result=self.providers.call("repair: return outcome revised with the complete corrected proposal, or cannot_repair, or capability_gap",payload,RepairOutput,run)
                if (result.outcome=="revised")!=(result.revised_proposal is not None):
                    raise AppError("invalid_repair_output","A repair must include a proposal exactly when its outcome is revised")
                if result.outcome!="revised":
                    response=dict(run_id=run,version=view["version"],repair_outcome=result.outcome,explanation=result.explanation)
                    self.store.finish_run(run,response)
                    return response
                proposal=result.revised_proposal
            else:
                result=self.providers.call("revision: produce exactly one replacement or capability gaps",payload,GenerationOutput,run)
                if result.capability_gaps:
                    response=dict(run_id=run,version=view["version"],capability_gaps=[g.model_dump() for g in result.capability_gaps])
                    self.store.finish_run(run,response)
                    return response
                if len(result.proposals)!=1:
                    raise AppError("invalid_revision","A revision must return exactly one proposal")
                proposal=result.proposals[0]
            content=self.tidy(run,canonical_content(proposal))
            try:
                fields=validate_proposal(content,spec["inventory"],scope)
                if scope is not None:
                    fields["generation_scope"]=scope
                if constraint:
                    constraint(view["content"],content.model_dump())
                if digest(content.model_dump()) == digest(canonical_content(ProposalContent.model_validate(view["content"])).model_dump()):
                    raise AppError("revision_not_changed", "The model did not change the proposal. The requested revision is not complete; the previous version is preserved")
            except AppError as exc:
                labels={o["id"]:f'{o["method"]} {o["path"]}' for o in spec["inventory"]["operations"]}
                exc.details["candidate_changes"]=change_summary(view["content"],content.model_dump(),labels,[])
                raise
            with self.store.connect(write=True) as c:
                self.check_current(c,pid,view["version"],submission.expected_revision,True)
                version=view["version"]+1
                c.execute("UPDATE versions SET state='superseded',review_revision=review_revision+1 WHERE proposal_id=? AND version=?",(pid,view["version"]))
                c.execute("UPDATE proposals SET current_version=? WHERE id=?",(version,pid))
                c.execute("INSERT INTO versions(proposal_id,version,spec_id,content,derived,state,run_id,created_at) VALUES(?,?,?,?,?,'needs_clarification',?,?)",(pid,version,view["spec_id"],dump(content.model_dump()),dump(fields),run,now()))
                state=self.settle(c,pid,version,content,fields)
                response=dict(run_id=run,version=version,state=state)
                c.execute("UPDATE runs SET status='succeeded',result=?,completed_at=? WHERE id=?",(dump(response),now(),run))
            return response
        except Exception as exc:
            self.fail_run(run,exc)
            raise

    def confirm_requirement(self, pid, version, requirement_id, submission):
        """Owner confirmation of an answer the current reconciliation assessed as sufficient."""
        with self.store.connect(write=True) as c:
            row = self.check_current(c,pid,version,submission.expected_revision)
            reqs = requirements.ensure(c,pid,version)
            if requirement_id not in {r["id"] for r in reqs}:
                raise AppError("unknown_requirement","This version has no such access requirement",404)
            answers = {a["question_id"]:dict(a) for a in c.execute("SELECT * FROM answers WHERE proposal_id=? AND version=? ORDER BY id",(pid,version))}
            current = next(r for r in requirements.current(c,pid,version,reqs,answers,row["reconciliation_id"]) if r["id"] == requirement_id)
            if current["status"] != "answer_sufficient":
                raise AppError("confirmation_blocked",f"Only an answer the current reconciliation assessed as sufficient can be confirmed (status: {current['status']})")
            requirements.event(c,pid,version,requirement_id,"owner_confirmed",self.settings.dev_reviewer_id,"Owner confirmed this answer as intended behavior; runtime enforcement is not implemented.","owner_confirmed",current["answer"]["id"],row["reconciliation_id"])
            c.execute("UPDATE versions SET review_revision=review_revision+1 WHERE proposal_id=? AND version=?",(pid,version))
        return self.view(pid,version)

    def supersede_question(self, pid, version, question_id, submission):
        """Replace a version with one without an invalid question; the question, reason and answers stay in history."""
        view = self.view(pid,version)
        old = ProposalContent.model_validate(view["content"])
        question = next((q for q in old.questions if q.id == question_id),None)
        if not question:
            raise AppError("unknown_question","This version has no such question",404)
        if question.configuration_key:
            raise AppError("question_configuration","This question establishes a configuration value; request a revision instead")
        content = canonical_content(old.model_copy(update=dict(questions=[q for q in old.questions if q.id != question_id])))
        spec = self.spec(view["spec_id"])
        scope = view["derived"].get("generation_scope")
        fields = validate_proposal(content,spec["inventory"],scope)
        if scope is not None:
            fields["generation_scope"] = scope
        gaps = view["derived"].get("generation_capability_gaps",[])
        fields["generation_capability_gaps"] = gaps
        fields["blockers"].extend("Requires explicit revision: " + g["explanation"] for g in gaps)
        run = self.store.start_run(view["business_id"],"question_supersession",dict(proposal_id=pid,version=version,question_id=question_id,reason=submission.reason))
        try:
            with self.store.connect(write=True) as c:
                self.check_current(c,pid,version,submission.expected_revision)
                new_version = version+1
                c.execute("UPDATE versions SET state='superseded',review_revision=review_revision+1 WHERE proposal_id=? AND version=?",(pid,version))
                c.execute("UPDATE proposals SET current_version=? WHERE id=?",(new_version,pid))
                c.execute("INSERT INTO versions(proposal_id,version,spec_id,content,derived,state,run_id,created_at) VALUES(?,?,?,?,?,'needs_clarification',?,?)",(pid,new_version,view["spec_id"],dump(content.model_dump()),dump(fields),run,now()))
                c.execute("INSERT INTO question_supersessions VALUES(?,?,?,?,?,?,?,?,?)",(uid(),pid,version,question_id,question.text,submission.reason.strip(),self.settings.dev_reviewer_id,new_version,now()))
                for a in view["answers"].values():
                    if a["question_id"] != question_id:
                        c.execute("INSERT INTO answers(proposal_id,version,question_id,text,reviewer,created_at) VALUES(?,?,?,?,?,?)",(pid,new_version,a["question_id"],a["text"],a["reviewer"],now()))
                state = self.settle(c,pid,new_version,content,fields)
                response = dict(run_id=run,version=new_version,state=state)
                c.execute("UPDATE runs SET status='succeeded',result=?,completed_at=? WHERE id=?",(dump(response),now(),run))
            return response
        except Exception as exc:
            self.fail_run(run,exc)
            raise

    def approval(self, pid, version, content_sha256=None):
        """The approve_to_build decision for this exact, still-current version, or an error."""
        row = self.store.one("SELECT v.*,p.current_version,p.business_id FROM versions v JOIN proposals p ON p.id=v.proposal_id WHERE v.proposal_id=? AND v.version=?", (pid, version))
        decisions = self.store.all("SELECT * FROM decisions WHERE proposal_id=? AND version=? AND action='approve_to_build'", (pid, version))
        content = json.loads(row["content"])
        spec = self.spec(row["spec_id"])
        if row["current_version"] != version or row["state"] != "approved_to_build" or not decisions:
            raise AppError("approval_not_current", "Only the current version with an approve_to_build decision can be built or run; a changed proposal needs fresh approval", 409)
        snapshot = json.loads(decisions[0]["snapshot"])
        if digest(snapshot["content"]) != digest(content) or snapshot["checksum"] != spec["checksum"] or (content_sha256 and content_sha256 != digest(content)):
            raise AppError("approval_not_current", "The approved snapshot no longer matches this version or its specification", 409)
        return row, content, spec, decisions[0]

    def approved_context(self, pid, connector_id):
        view = self.view(pid)
        row, content, spec, decision = self.approval(pid, view["version"])
        connector = load_connectors(self.settings).get(connector_id)
        if not connector or connector.get("business_id") != row["business_id"]:
            raise AppError("connector_unknown", "No configured connector with this id serves this business", 404)
        connector = dict(id=connector_id, base_url=connector["base_url"].rstrip("/"), context_fields=sorted(connector.get("context_fields") or []))
        return view, row, content, spec, decision, connector, view["derived"].get("generation_scope")

    def enforcement(self, eid):
        row = self.store.one("SELECT * FROM enforcement_configs WHERE id=?", (eid,))
        row["content"] = json.loads(row["content"])
        latest = self.store.one("SELECT id FROM enforcement_configs WHERE proposal_id=? AND version=? ORDER BY rowid DESC LIMIT 1", (row["proposal_id"], row["version"]))["id"]
        row["status"] = "superseded" if latest != eid else "approved" if row["reviewed_at"] else "awaiting_review"
        return row

    def current_enforcement(self, pid, version):
        rows = self.store.all("SELECT id FROM enforcement_configs WHERE proposal_id=? AND version=? ORDER BY rowid DESC LIMIT 1", (pid, version))
        return self.enforcement(rows[0]["id"]) if rows else None

    def submit_enforcement(self, pid, submission):
        """Operator-proposed mechanisms, validated now and usable only after owner review."""
        view, row, content, spec, _, connector, scope = self.approved_context(pid, submission.connector_id)
        validate_enforcement(submission.enforcement, content, self.scoped(spec["inventory"], scope), view["requirements"], connector)
        sha = digest(dict(connector=connector, enforcement=submission.enforcement))
        current = self.current_enforcement(pid, row["version"])
        if current and current["sha256"] == sha:
            return current
        eid = uid()
        with self.store.connect(write=True) as c:
            c.execute("INSERT INTO enforcement_configs(id,proposal_id,version,connector_id,content,sha256,submitted_by,submitted_at) VALUES(?,?,?,?,?,?,?,?)",
                      (eid, pid, row["version"], submission.connector_id, dump(dict(connector=connector, enforcement=submission.enforcement)), sha, self.settings.dev_reviewer_id, now()))
        return self.enforcement(eid)

    def review_enforcement(self, eid, submission):
        config = self.enforcement(eid)
        self.approval(config["proposal_id"], config["version"])
        if config["status"] == "superseded":
            raise AppError("enforcement_superseded", "A newer enforcement configuration exists; review that one", 409)
        if config["status"] == "awaiting_review":
            with self.store.connect(write=True) as c:
                c.execute("UPDATE enforcement_configs SET reviewed_by=?,reviewed_at=?,review_note=? WHERE id=? AND reviewed_at IS NULL", (self.settings.dev_reviewer_id, now(), submission.note.strip(), eid))
        return self.enforcement(eid)

    def build_artifact(self, pid, submission):
        view, row, content, spec, decision, connector, scope = self.approved_context(pid, submission.connector_id)
        fields = validate_proposal(ProposalContent.model_validate(content), spec["inventory"], scope)
        if fields["blockers"]:
            raise AppError("artifact_not_approved", "Grounding reports blockers: " + "; ".join(fields["blockers"]))
        config = self.current_enforcement(pid, row["version"])
        if config and config["content"]["connector"] != connector:
            raise AppError("enforcement_connector", "The reviewed enforcement was configured for a different connector or connector context; submit it again for review", 409)
        if config and config["status"] != "approved":
            raise AppError("enforcement_unreviewed", "The current enforcement configuration awaits owner review", 409)
        proposal = dict(id=pid, version=row["version"], business_id=row["business_id"], content_sha256=digest(content), decision_id=decision["id"],
                        decided_at=decision["created_at"], reviewer=decision["reviewer"], reconciliation_id=row["reconciliation_id"])
        enforcement = dict(config, content=config["content"]["enforcement"]) if config else None
        artifact, sha = compile_artifact(content, fields, self.scoped(spec["inventory"], scope), view["requirements"], proposal,
                                         dict(spec_id=spec["id"], spec_sha256=spec["checksum"]), connector, enforcement)
        with self.store.connect(write=True) as c:
            existing = c.execute("SELECT id FROM artifacts WHERE proposal_id=? AND version=? AND sha256=?", (pid, row["version"], sha)).fetchone()
            aid = existing["id"] if existing else uid()
            if not existing:
                c.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)", (aid, pid, row["version"], decision["id"], sha, dump(artifact), now()))
        return self.artifact(aid)

    def artifact(self, aid):
        row = self.store.one("SELECT * FROM artifacts WHERE id=?", (aid,))
        row["content"] = json.loads(row["content"])
        if digest(row["content"]) != row["sha256"]:
            raise AppError("artifact_integrity", "Stored artifact content does not match its hash", 409)
        row["executions"] = [dict(e, report=json.loads(e["report"]) if e["report"] else None) for e in self.store.all("SELECT * FROM executions WHERE artifact_id=? ORDER BY created_at", (aid,))]
        row["tests"] = [dict(t, expectation=json.loads(t["expectation"]), failure_report=json.loads(t["failure_report"]) if t["failure_report"] else None) for t in self.store.all("SELECT * FROM sandbox_tests WHERE artifact_id=? ORDER BY created_at", (aid,))]
        return row

    def run_sandbox(self, aid, submission, mode="sandbox", audit=None, precheck=None):
        """One sandbox execution; pre-flight rejections are recorded and send no request."""
        eid = uid()
        audit = audit or {}
        record = lambda status, report, done=True: dict(id=eid, owner=OWNER, artifact_id=aid, mode=mode, identity=submission.identity, status=status, report=dump(dict(report, **audit)) if report else None, created_at=now(), completed_at=now() if done else None)
        artifact = self.artifact(aid)["content"]
        try:
            if precheck:
                precheck()
            self.approval(artifact["proposal"]["id"], artifact["proposal"]["version"], artifact["proposal"]["content_sha256"])
            self.check_enforcement(artifact)
            if artifact["execution_blockers"]:
                raise AppError("access_enforcement_missing", "Execution is blocked until every access requirement has an enforceable mechanism", 409, {"blockers": artifact["execution_blockers"]})
            connector = load_connectors(self.settings).get(artifact["connector"]["id"])
            if not connector:
                raise AppError("connector_unknown", "The artifact's connector is not configured", 404)
            base = destination(connector, artifact, self.settings)
            identity = (connector.get("identities") or {}).get(submission.identity)
            credentials(identity, artifact)
            validate_arguments(artifact["input_schema"], submission.arguments)
        except AppError as exc:
            report = dict(status="rejected", code=exc.code, message=exc.message, requests_sent=0, arguments=sorted(submission.arguments), details={k: v for k, v in exc.details.items() if k in ("errors", "blockers")})
            with self.store.connect(write=True) as c:
                c.execute("INSERT INTO executions(id,artifact_id,mode,identity,status,report,created_at,completed_at,owner) VALUES(:id,:artifact_id,:mode,:identity,:status,:report,:created_at,:completed_at,:owner)", record("rejected", report))
            exc.details["execution_id"] = eid
            raise
        with self.store.connect(write=True) as c:
            c.execute("INSERT INTO executions(id,artifact_id,mode,identity,status,report,created_at,completed_at,owner) VALUES(:id,:artifact_id,:mode,:identity,:status,:report,:created_at,:completed_at,:owner)", record("running", None, False))
        try:
            report, outputs = Run(artifact, submission.arguments, identity, base, self.execution_transport).execute()
        except Exception:
            with self.store.connect(write=True) as c:
                c.execute("UPDATE executions SET status='interrupted',completed_at=?,report=? WHERE id=?", (now(), dump(dict(INTERRUPTED, message="The execution stopped on an unexpected error. A write may or may not have been applied; check the target before retrying. Nothing was retried.", **audit)), eid))
            raise
        with self.store.connect(write=True) as c:
            c.execute("UPDATE executions SET status=?,report=?,completed_at=? WHERE id=?", (report["status"], dump(dict(report, **audit)), now(), eid))
        return dict(execution_id=eid, artifact_id=aid, report=report, outputs=outputs)

    def publication(self, pub_id):
        row = self.store.one("SELECT * FROM publications WHERE id=?", (pub_id,))
        return self.publication_view(row)

    def publication_view(self, row):
        row = dict(row, evidence=json.loads(row["evidence"]), production_activation=False, production_ready=False)
        problem = None
        if row["status"] == "published":
            try:
                self.check_publication(row)
            except AppError as exc:
                problem = dict(code=exc.code, message=exc.message)
        row["effective_status"] = row["status"] if not problem else "blocked"
        row["problem"] = problem
        return row

    def publications(self, business_id=None, artifact_id=None):
        rows = self.store.all("SELECT * FROM publications WHERE (? IS NULL OR business_id=?) AND (? IS NULL OR artifact_id=?) ORDER BY published_at",
                              (business_id, business_id, artifact_id, artifact_id))
        return [self.publication_view(r) for r in rows]

    def publication_problems(self, aid):
        """Everything that would block publishing this exact artifact now (empty when it may be published)."""
        a = self.artifact(aid)
        c = a["content"]
        problems = []
        try:
            _, _, _, decision = self.approval(c["proposal"]["id"], c["proposal"]["version"], c["proposal"]["content_sha256"])
            if decision["id"] != c["proposal"]["decision_id"]:
                raise AppError("approval_not_current", "The artifact was built under a different approval decision", 409)
            self.check_enforcement(c)
            if c["execution_blockers"]:
                raise AppError("access_enforcement_missing", "Execution is blocked until every access requirement has an enforceable mechanism", 409)
            connector = load_connectors(self.settings).get(c["connector"]["id"])
            if not connector:
                raise AppError("connector_unknown", "The artifact's connector is not configured", 404)
            destination(connector, c, self.settings)
        except AppError as exc:
            problems.append(f"{exc.code}: {exc.message}")
        missing, evidence = publishing.evidence(c, a["tests"], a["executions"])
        return a, problems + missing, evidence

    def check_enforcement(self, content):
        current = self.current_enforcement(content["proposal"]["id"], content["proposal"]["version"])
        built = (content["enforcement_config"] or {}).get("id")
        if built != (current["id"] if current else None) or (current and current["status"] != "approved"):
            raise AppError("enforcement_not_current", "The enforcement configuration changed after this artifact was built; review it and rebuild", 409)
        return current

    def publish(self, aid, submission):
        """Explicit TEST-ONLY sandbox publication of one exact, approved and tested artifact (idempotent while active)."""
        a, problems, evidence = self.publication_problems(aid)
        if problems:
            raise AppError("publication_not_allowed", "This artifact cannot be published: " + "; ".join(problems), 409, {"problems": problems})
        c = a["content"]
        enforcement = c["enforcement_config"] or {}
        with self.store.connect(write=True) as conn:
            active = conn.execute("SELECT id FROM publications WHERE artifact_id=? AND status='published'", (aid,)).fetchone()
            pub_id = active["id"] if active else uid()
            if not active:
                conn.execute("INSERT INTO publications(id,artifact_id,proposal_id,version,business_id,artifact_sha256,decision_id,enforcement_config_id,enforcement_sha256,evidence,environment,tool_name,note,publisher,published_at,status) "
                             "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'published')",
                             (pub_id, aid, a["proposal_id"], a["version"], c["proposal"]["business_id"], a["sha256"], c["proposal"]["decision_id"], enforcement.get("id"),
                              enforcement.get("sha256"), dump(evidence), submission.environment, publishing.tool_name(aid, c), submission.note.strip(), self.settings.dev_reviewer_id, now()))
        return self.publication(pub_id)

    def disable_publication(self, pub_id, submission):
        """Blocks every later call; it cannot undo a write that already completed or is in flight."""
        with self.store.connect(write=True) as conn:
            conn.execute("UPDATE publications SET status='disabled',disabled_by=?,disabled_at=?,disable_note=? WHERE id=? AND status='published'",
                         (self.settings.dev_reviewer_id, now(), submission.note.strip(), pub_id))
        return self.publication(pub_id)

    def check_publication(self, pub):
        """Re-read on every list and call: still published, same artifact bytes, same approval, same enforcement, evidence intact."""
        row = self.store.one("SELECT * FROM publications WHERE id=?", (pub["id"],))
        if row["status"] != "published":
            raise AppError("publication_disabled", "This tool's sandbox publication was disabled", 409)
        a = self.artifact(row["artifact_id"])
        c = a["content"]
        if a["sha256"] != row["artifact_sha256"]:
            raise AppError("artifact_integrity", "The artifact differs from the one that was published", 409)
        _, _, _, decision = self.approval(c["proposal"]["id"], c["proposal"]["version"], c["proposal"]["content_sha256"])
        if decision["id"] != row["decision_id"]:
            raise AppError("approval_not_current", "The approval recorded at publication is no longer current", 409)
        current = self.check_enforcement(c)
        if (current["id"] if current else None) != row["enforcement_config_id"]:
            raise AppError("enforcement_not_current", "The enforcement recorded at publication is no longer current", 409)
        problems, _ = publishing.evidence(c, a["tests"], a["executions"])
        if problems:
            raise AppError("publication_evidence", "Sandbox test evidence for this artifact no longer holds: " + "; ".join(problems), 409)
        return a

    def published_tools(self, business_id, identity):
        """MCP tool definitions for publications that are valid right now and usable by this server's identity."""
        tools = []
        for row in self.store.all("SELECT * FROM publications WHERE business_id=? AND status='published' ORDER BY published_at", (business_id,)):
            try:
                c = self.check_publication(row)["content"]
            except AppError:
                continue
            if identity in ((load_connectors(self.settings).get(c["connector"]["id"]) or {}).get("identities") or {}):
                tools.append(publishing.tool(row, c))
        return tools

    def invoke_published(self, business_id, identity, name, arguments):
        """One MCP tool call through the existing executor; identity comes from the server process, never from arguments."""
        rows = self.store.all("SELECT * FROM publications WHERE tool_name=? AND business_id=? ORDER BY published_at DESC LIMIT 1", (name, business_id))
        if not rows or rows[0]["status"] != "published":
            raise AppError("tool_not_published", "No sandbox-published tool with this name is available to this server", 404)
        pub = rows[0]
        audit = dict(channel="mcp_stdio", publication_id=pub["id"], tool_name=name)
        try:
            result = self.run_sandbox(pub["artifact_id"], SandboxRunSubmission(identity=identity, arguments=arguments or {}), "mcp_sandbox", audit, lambda: self.check_publication(pub))
        except AppError as exc:
            if "execution_id" not in exc.details:
                raise
            return publishing.rejected(pub, exc)
        return publishing.executed(pub, result)

    def run_sandbox_test(self, aid, submission):
        """One sandbox run judged against an operator-authored expectation; failures get a structured report."""
        a = self.artifact(aid)
        self.check_scenario(a, submission)
        selectors = self.record_selectors(a)
        selector = digest(dict(artifact=aid, values={k: submission.arguments.get(k) for k in selectors})) if isinstance(submission.arguments, dict) else None
        try:
            result = self.run_sandbox(aid, SandboxRunSubmission(identity=submission.identity, arguments=submission.arguments))
            eid, report, outputs = result["execution_id"], result["report"], result["outputs"] or {}
        except AppError as exc:
            if "execution_id" not in exc.details:
                raise
            eid, report, outputs = exc.details["execution_id"], dict(status="rejected", code=exc.code), {}
        content = json.loads(self.store.one("SELECT content FROM versions WHERE proposal_id=? AND version=?", (a["proposal_id"], a["version"]))["content"])
        verdict, failure = repairs.evaluate(a["content"], content, submission.name, submission.expect.model_dump(), report, outputs)
        if failure:
            failure.update(artifact_id=aid, proposal_id=a["proposal_id"], version=a["version"])
        tid = uid()
        with self.store.connect(write=True) as c:
            c.execute("INSERT INTO sandbox_tests(id,artifact_id,execution_id,name,expectation,verdict,failure_report,created_at,scenario,record_owner,selector_sha256) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (tid, aid, eid, submission.name, dump(submission.expect.model_dump()), verdict, dump(failure) if failure else None, now(), submission.scenario, submission.record_owner, selector))
        return dict(test_id=tid, verdict=verdict, execution_id=eid, status=report["status"], failure_report=failure, scenario=submission.scenario)

    def check_scenario(self, a, submission):
        """Access-control scenarios are explicit; nothing is inferred from a status code alone."""
        bad = lambda message: AppError("invalid_scenario", message)
        if submission.scenario == "own_record" and submission.expect.status != "succeeded":
            raise bad("An own_record test must expect success: it shows the identity's credential works and it may use its own record")
        if submission.scenario != "cross_user":
            if submission.record_owner:
                raise bad("record_owner applies only to cross_user tests")
            return
        if submission.expect.status != "failed":
            raise bad("A cross_user test must expect a refused (failed) run")
        identities = (load_connectors(self.settings).get(a["content"]["connector"]["id"]) or {}).get("identities") or {}
        if not submission.record_owner or submission.record_owner == submission.identity or submission.record_owner not in identities:
            raise bad("A cross_user test names record_owner: another configured identity of this connector whose record the arguments target")

    def record_selectors(self, a):
        """Runtime arguments that select records under this version's record-scope requirements."""
        view = self.view(a["proposal_id"], a["version"])
        fields = {r["field"] for r in view["requirements"] if r["kind"] == "record_scope" and r["field"]}
        names = set()
        for s in a["content"]["steps"]:
            inputs = [(f'{p["location"]}.{p["name"]}', p["source"]) for p in s["parameters"]] + [(f'body.{f["name"]}', f["source"]) for f in (s["body"] or {}).get("fields", [])]
            names |= {src["reference"] for key, src in inputs if key in fields and src["kind"] == "runtime_argument"}
        return sorted(names)

    def repair(self, test_id, submission):
        """At most MAX_ATTEMPTS model repairs per failing case; each success is a new unapproved version."""
        test = self.store.one("SELECT t.*,a.proposal_id,a.version FROM sandbox_tests t JOIN artifacts a ON a.id=t.artifact_id WHERE t.id=?", (test_id,))
        if test["verdict"] == "passed":
            raise AppError("nothing_to_repair", "This sandbox test passed")
        report, pid = json.loads(test["failure_report"]), test["proposal_id"]
        view = self.view(pid)
        attempts = self.store.one("SELECT COUNT(*) AS n FROM repairs WHERE proposal_id=? AND case_name=? AND run_id IS NOT NULL", (pid, test["name"]))["n"]
        def record(outcome, detail, run=None, new_version=None):
            rid = uid()
            with self.store.connect(write=True) as c:
                c.execute("INSERT INTO repairs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (rid, pid, test_id, test["name"], attempts + (1 if run else 0), test["version"], outcome, detail, submission.verification, run, new_version, now()))
            return rid
        def refuse(code, outcome, message, status=409, **details):
            raise AppError(code, message, status, dict(details, repair_id=record(outcome, message), classification=report["classification"]))
        if view["version"] != test["version"]:
            refuse("repair_stale", "stale", "The proposal has a newer version; rerun the test on that version's artifact")
        if not report["repairable"]:
            refuse("repair_not_applicable", "not_repairable", report["recommended_action"], 422)
        if report["requires_verification"] and not (submission.verification or "").strip():
            refuse("verification_required", "verification_required", "The API rejected a write. Record independent evidence that nothing changed before repairing; a rejection alone does not prove it")
        if attempts >= repairs.MAX_ATTEMPTS:
            refuse("repair_exhausted", "exhausted", f"{repairs.MAX_ATTEMPTS} repair attempts were used for case {test['name']}; owner revision or clarification is required")
        earlier = self.store.all("SELECT r.attempt,r.from_version,r.outcome,r.detail,t.failure_report FROM repairs r JOIN sandbox_tests t ON t.id=r.test_id "
                                 "WHERE r.proposal_id=? AND r.case_name=? AND r.run_id IS NOT NULL ORDER BY r.created_at", (pid, test["name"]))
        history = [dict(attempt=r["attempt"], version=r["from_version"], failure=repairs.describe(json.loads(r["failure_report"])), result=r["outcome"], feedback=r["detail"]) for r in earlier]
        spec = self.spec(view["spec_id"])
        contract = repairs.evidence(report, view["content"], {o["id"]: o for o in spec["inventory"]["operations"]})
        try:
            response = self.revise(pid, RevisionSubmission(expected_revision=view["review_revision"], instruction=repairs.instruction(report, contract, history)), repairs.check_scope, "automated_repair", repair=True)
        except AppError as exc:
            exc.details["repair_id"] = record(exc.code, repairs.feedback(exc, report), exc.details.get("run_id"))
            raise
        if response.get("repair_outcome"):
            label = dict(capability_gap="Model reported a missing capability; owner clarification or a new operation is required: ",
                         cannot_repair="Model reported that rewiring the existing steps cannot meet the expected behavior; owner revision is required: ")[response["repair_outcome"]]
            detail = label + response["explanation"]
            return dict(outcome=response["repair_outcome"], repair_id=record(response["repair_outcome"], detail, response["run_id"]), detail=detail, attempt=attempts + 1)
        changes = self.view(pid)["change_summary"]
        rid = record("revised", f'Version {response["version"]} created (changes: {"; ".join(changes)}); it needs fresh answers, reconciliation, confirmation, approval, enforcement review and a rebuild before the test is rerun', response["run_id"], response["version"])
        return dict(outcome="revised", repair_id=rid, attempt=attempts + 1, version=response["version"], state=response["state"], changes=changes)

    def current_contents(self, business_id):
        rows = self.store.all("SELECT p.id AS proposal_id,v.version,v.state,v.content FROM proposals p JOIN versions v ON v.proposal_id=p.id AND v.version=p.current_version "
                              "WHERE p.business_id=? ORDER BY v.created_at", (business_id,))
        return [dict(r, content=json.loads(r["content"])) for r in rows]

    def existing_tools(self, business_id):
        built = {r["proposal_id"] for r in self.store.all("SELECT DISTINCT a.proposal_id FROM artifacts a JOIN proposals p ON p.id=a.proposal_id WHERE p.business_id=?", (business_id,))}
        live = {r["proposal_id"] for r in self.store.all("SELECT DISTINCT proposal_id FROM publications WHERE business_id=? AND status='published'", (business_id,))}
        return [dict(proposal_id=r["proposal_id"], name=r["content"]["name"], purpose=r["content"]["business_purpose"], state=r["state"], version=r["version"],
                     operation_ids=[s["operation_id"] for s in r["content"]["steps"]], built=r["proposal_id"] in built, published=r["proposal_id"] in live)
                for r in self.current_contents(business_id)]

    def request_spec(self, business_id, spec_id=None):
        self.store.one("SELECT id FROM businesses WHERE id=?", (business_id,))
        for row in self.store.all("SELECT id FROM specifications WHERE business_id=? ORDER BY created_at DESC", (business_id,)):
            if spec_id and row["id"] != spec_id:
                continue
            spec = self.spec(row["id"])
            if spec["inventory"].get("source_kind") != "code" and spec["inventory"]["proposal_generation_ready"]:
                return spec
        raise AppError("no_inventory", "Discover the business system (a valid OpenAPI description with proposal-eligible operations) first", 409)

    def area_row(self, spec_id):
        rows = self.store.all("SELECT * FROM spec_areas WHERE spec_id=?", (spec_id,))
        return rows[0] if rows else None

    def areas(self, spec_id):
        """Business areas of one API description, the owner's selection, and how many operations fit one model batch."""
        spec = self.spec(spec_id)
        inventory = spec["inventory"]
        groups = {g["key"]: g for g in business_areas.base_groups(inventory)}
        row = self.area_row(spec_id)
        content = json.loads(row["content"]) if row else dict(areas=business_areas.from_groups(groups.values(), "From the API's own tags or paths"), error=None)
        states = {o["id"]: capabilities.status(o) for o in inventory["operations"]}
        for a in content["areas"]:
            a["operation_ids"] = [i for k in a["group_keys"] if k in groups for i in groups[k]["operation_ids"]]
            a["groups"] = [groups[k]["label"] for k in a["group_keys"] if k in groups]
            a["counts"] = {s: sum(1 for i in a["operation_ids"] if states[i] == s) for s in ("eligible", "restricted", "unsupported")}
        selected = json.loads(row["selected"]) if row and row["selected"] else None
        included = None if selected is None else sorted({i for a in content["areas"] if a["id"] in selected for i in a["operation_ids"]})
        index, coverage, _ = self.fitted_index("request_triage", lambda index, coverage: dict(operation_index=dict(operations=index, coverage=capabilities.model_coverage(coverage))),
                                               inventory, None if included is None else set(included))
        source = row["source"] if row else "api"
        # AI areas are in priority order: suggest the most useful ones that together fit about one batch.
        suggested, total = [], 0
        for a in content["areas"] if source == "ai" and selected is None else []:
            if not a["counts"]["eligible"]:
                continue
            if suggested and total + len(a["operation_ids"]) > coverage["considered"]:
                break
            suggested.append(a["id"])
            total += len(a["operation_ids"])
        return dict(spec_id=spec_id, source=source, run_id=row["run_id"] if row else None, error=content.get("error"), areas=content["areas"],
                    selected=selected, selected_by=row["selected_by"] if row else None, selected_at=row["selected_at"] if row else None,
                    suggested=suggested, included_ids=included, in_scope=coverage["total_operations"], fits=coverage["considered"])

    def area_scope(self, spec_id):
        """Operation ids of the owner's selected areas, or None when no selection was saved (every operation)."""
        row = self.area_row(spec_id)
        return None if not row or not row["selected"] else set(self.areas(spec_id)["included_ids"])

    def organize_areas(self, spec_id):
        """Ask the model to organize the API's own groups into business areas; on failure keep the API groups and show why."""
        spec = self.spec(spec_id)
        if spec["inventory"].get("source_kind") == "code" or not spec["inventory"]["operations"]:
            raise AppError("areas_unavailable", "Business areas need an OpenAPI description with operations", 409)
        if not self.organizing.acquire(blocking=False):
            raise AppError("areas_busy", "The AI is already organizing business areas; wait for it to finish", 409)
        try:
            return self._organize_areas(spec_id, spec)
        finally:
            self.organizing.release()

    def checked_run(self, business_id, kind, payload, output_model, check):
        """A model run whose output must also pass `check`; a failed check fails the run."""
        run, output = self.model_run(business_id, kind, payload, output_model)
        try:
            result = check(output)
        except AppError as exc:
            self.fail_run(run, exc)
            raise
        self.store.finish_run(run, dict(items=len(result)))
        return run, result

    def _organize_areas(self, spec_id, spec):
        """Two small steps an 8B model handles: name and rank the areas, then assign groups in batches."""
        groups = business_areas.base_groups(spec["inventory"])
        b = self.store.one("SELECT * FROM businesses WHERE id=?", (spec["business_id"],))
        business = dict(name=b["name"], description=b["description"])
        run = None
        try:
            run, named = self.checked_run(spec["business_id"], "area_naming", dict(business=business, groups=business_areas.model_groups(groups, 0)),
                                          AreaNamingOutput, business_areas.named_areas)
            areas = [dict(name=a["name"], description=a["description"]) for a in named]
            assigned = {}
            for start in range(0, len(groups), business_areas.ASSIGN_BATCH):
                batch = groups[start:start + business_areas.ASSIGN_BATCH]
                for samples in (3, 1, 0):
                    payload = dict(business=business, areas=areas, groups=business_areas.model_groups(batch, samples))
                    if input_fits(self.settings, "area_assignment", payload):
                        break
                _, part = self.checked_run(spec["business_id"], "area_assignment", payload, AreaAssignmentOutput,
                                           lambda output: business_areas.assignment(output, batch, named))
                assigned.update(part)
            content, source = dict(areas=business_areas.from_assignments(named, assigned, groups), error=None), "ai"
        except AppError as exc:
            run = exc.details.get("run_id", run)
            content, source = dict(areas=business_areas.from_groups(groups, "From the API's own tags or paths"), error=dict(code=exc.code, message=exc.message)), "api"
        with self.store.connect(write=True) as c:
            c.execute("INSERT INTO spec_areas(spec_id,content,source,run_id,created_at) VALUES(?,?,?,?,?) ON CONFLICT(spec_id) DO UPDATE SET "
                      "content=excluded.content,source=excluded.source,run_id=excluded.run_id,selected=NULL,selected_by=NULL,selected_at=NULL,created_at=excluded.created_at",
                      (spec_id, dump(content), source, run, now()))
        return self.areas(spec_id)

    def select_areas(self, spec_id, submission):
        """Record which business areas the model may see for requests, suggestions and generation batches."""
        view = self.areas(spec_id)
        ids = list(dict.fromkeys(submission.area_ids))
        if not set(ids) <= {a["id"] for a in view["areas"]}:
            raise AppError("unknown_area", "Select areas shown for this API description")
        if not any(a["counts"]["eligible"] for a in view["areas"] if a["id"] in ids):
            raise AppError("area_selection", "Select at least one area with operations that can be used for tools")
        stamp = now()
        with self.store.connect(write=True) as c:
            if not c.execute("SELECT 1 FROM spec_areas WHERE spec_id=?", (spec_id,)).fetchone():
                stored = [{k: a[k] for k in ("id", "name", "description", "audience", "reason", "group_keys")} for a in view["areas"]]
                c.execute("INSERT INTO spec_areas(spec_id,content,source,created_at) VALUES(?,?,?,?)", (spec_id, dump(dict(areas=stored, error=None)), "api", stamp))
            c.execute("UPDATE spec_areas SET selected=?,selected_by=?,selected_at=? WHERE spec_id=?", (dump(ids), self.settings.dev_reviewer_id, stamp, spec_id))
        return self.areas(spec_id)

    def fitted_index(self, kind, build, inventory, include_ids=None, exclude_ids=()):
        """Operation index whose complete model input passes the provider size checks, shrinking the budget when needed."""
        budget = self.settings.capability_index_chars
        while True:
            index, coverage = capabilities.operation_index(inventory, budget, include_ids, exclude_ids)
            payload = build(index, coverage)
            if len(index) <= 1 or input_fits(self.settings, kind, payload):
                return index, coverage, payload
            budget = int(budget * 0.8)

    def generation_used(self, spec_id):
        return {i for r in self.store.all("SELECT operation_ids FROM generation_batches WHERE spec_id=?", (spec_id,)) for i in json.loads(r["operation_ids"])}

    def next_generation_batch(self, spec_id):
        """Unused eligible operations of the selected areas, in area order, while the generation input still fits the model."""
        spec, view = self.spec(spec_id), self.areas(spec_id)
        inventory = spec["inventory"]
        ok = {o["id"] for o in inventory["operations"] if o.get("proposal_eligible", o.get("supported", True))}
        used, scope = self.generation_used(spec_id), view["included_ids"]
        order = [i for a in view["areas"] for i in a["operation_ids"] if (scope is None or i in scope) and i in ok and i not in used]
        business = self.store.one("SELECT * FROM businesses WHERE id=?", (spec["business_id"],))
        batch = []
        for op_id in order:
            trial = batch + [op_id]
            if not input_fits(self.settings, "generation", dict(business=business, inventory=self.scoped(inventory, trial), contract_version="1")):
                break
            batch = trial
        return dict(operation_ids=batch, remaining=len(order) - len(batch), used=len(used & ok))

    def tool_request(self, rid):
        row = self.store.one("SELECT * FROM tool_requests WHERE id=?", (rid,))
        for key in ("examples", "clarifications", "operation_scope", "run_ids", "proposal_ids", "existing_proposal_ids", "unresolved", "coverage", "outcome", "error"):
            if key != "examples" and row[key] is not None:
                row[key] = json.loads(row[key])
        tools = {t["proposal_id"]: t for t in self.existing_tools(row["business_id"])}
        row["proposals"] = [tools[p] for p in row["proposal_ids"] if p in tools]
        row["existing_tools"] = [tools[p] for p in row["existing_proposal_ids"] if p in tools]
        return row

    def tool_requests(self, business_id):
        return [self.tool_request(r["id"]) for r in self.store.all("SELECT id FROM tool_requests WHERE business_id=? ORDER BY created_at DESC", (business_id,))]

    def request_tool(self, business_id, submission, source="owner", suggestion_id=None, scope=None, clarifications=()):
        """Record an owner request once per idempotency key, then triage it; the proposal it yields enters normal review."""
        spec = self.request_spec(business_id, submission.spec_id)
        goal, examples = submission.goal.strip(), submission.examples.strip()
        request_hash = digest(dict(business=business_id, spec=spec["id"], goal=goal, examples=examples, source=source, suggestion=suggestion_id, scope=scope, clarifications=list(clarifications)))
        rid, stamp = uid(), now()
        notes = [dict(text=t, by=self.settings.dev_reviewer_id, at=stamp) for t in clarifications]
        with self.store.connect(write=True) as c:
            existing = c.execute("SELECT id,request_hash FROM tool_requests WHERE idempotency_key=?", (submission.idempotency_key,)).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise AppError("idempotency_conflict", "This request key was already used for a different request", 409)
                return self.tool_request(existing["id"])
            c.execute("INSERT INTO tool_requests(id,business_id,spec_id,source,suggestion_id,goal,examples,clarifications,operation_scope,idempotency_key,request_hash,status,run_ids,proposal_ids,existing_proposal_ids,unresolved,requester,created_at,updated_at) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?,?,'processing','[]','[]','[]','[]',?,?,?)",
                      (rid, business_id, spec["id"], source, suggestion_id, goal, examples, dump(notes), dump(scope) if scope is not None else None, submission.idempotency_key, request_hash, self.settings.dev_reviewer_id, stamp, stamp))
        return self.process_request(rid)

    def process_request(self, rid, exclude_ids=()):
        r = self.tool_request(rid)
        spec = self.spec(r["spec_id"])
        business = self.store.one("SELECT * FROM businesses WHERE id=?", (r["business_id"],))
        request = dict(id=rid, goal=r["goal"], examples=r["examples"], clarifications=[x["text"] for x in r["clarifications"]])
        existing = self.existing_tools(r["business_id"])
        build = lambda index, coverage: dict(business=business, owner_request={k: request[k] for k in ("goal", "examples", "clarifications")},
                                             operation_index=dict(operations=index, coverage=capabilities.model_coverage(coverage)), existing_tools=existing)
        index, coverage, payload = self.fitted_index("request_triage", build, spec["inventory"], self.area_scope(spec["id"]), exclude_ids)
        coverage["all_considered_ids"] = list(dict.fromkeys(list(exclude_ids) + coverage["considered_ids"]))
        runs, unresolved, fields = list(r["run_ids"]), [], dict(proposal_ids=[], existing_proposal_ids=[], error=None)
        try:
            if r["operation_scope"] is not None:
                scope, fields["outcome"] = r["operation_scope"], dict(summary="Accepted suggestion; its supporting operations were used without a separate triage.")
            else:
                run, triage = self.model_run(r["business_id"], "request_triage", payload, RequestTriageOutput)
                runs.append(run)
                try:
                    capabilities.check_triage(triage, index, existing)
                except AppError as exc:
                    self.fail_run(run, exc)
                    raise
                self.store.finish_run(run, triage.model_dump())
                unresolved = [dict(m.model_dump(), source="triage") for m in triage.missing] + [dict(kind="question", description=q, operation_ids=[], source="triage") for q in triage.questions]
                fields["outcome"] = dict(summary=triage.summary, triage=triage.outcome)
                if triage.outcome != "feasible":
                    status = dict(existing_tool="existing_tool", needs_clarification="needs_clarification", unavailable="unavailable")[triage.outcome]
                    return self.save_request(rid, status, runs, unresolved, coverage, dict(fields, existing_proposal_ids=triage.existing_proposal_ids))
                scope = triage.operation_ids
            result = self.generate(r["spec_id"], scope, request)
            runs += result.get("earlier_run_ids", []) + [result["run_id"]]
            unresolved += [dict(kind="capability_gap", description=g["explanation"], operation_ids=[], source="generation") for g in result["capability_gaps"]]
            status = "proposed" if result["proposal_ids"] else "existing_tool" if result["duplicates"] and not result["capability_gaps"] else "unavailable"
            return self.save_request(rid, status, runs, unresolved, coverage, dict(fields, proposal_ids=result["proposal_ids"], existing_proposal_ids=result["duplicates"]))
        except AppError as exc:
            for run in exc.details.get("earlier_run_ids", []) + [exc.details.get("run_id")]:
                if run and run not in runs:
                    runs.append(run)
            error = dict(code=exc.code, message=exc.message)
            if isinstance(exc.details.get("errors"), list):
                error["errors"] = check_errors(exc.details["errors"], (self.settings.openrouter_api_key, self.settings.session_secret))
            return self.save_request(rid, "failed", runs, unresolved, coverage, dict(fields, error=error))
        except Exception:
            return self.save_request(rid, "failed", runs, unresolved, coverage, dict(fields, error=dict(code="internal_error", message="Unexpected processing failure; retry the request")))

    def save_request(self, rid, status, runs, unresolved, coverage, fields):
        with self.store.connect(write=True) as c:
            c.execute("UPDATE tool_requests SET status=?,run_ids=?,proposal_ids=?,existing_proposal_ids=?,unresolved=?,coverage=?,outcome=?,error=?,updated_at=? WHERE id=?",
                      (status, dump(runs), dump(fields["proposal_ids"]), dump(fields["existing_proposal_ids"]), dump(unresolved), dump(coverage),
                       dump(fields.get("outcome")), dump(fields["error"]) if fields["error"] else None, now(), rid))
        return self.tool_request(rid)

    def clarify_request(self, rid, submission):
        """Add owner information to a request that could not become a proposal, then process it again."""
        text = submission.text.strip()
        with self.store.connect(write=True) as c:
            row = c.execute("SELECT status,clarifications FROM tool_requests WHERE id=?", (rid,)).fetchone()
            if not row:
                raise AppError("not_found", "Record not found", 404)
            if row["status"] not in ("needs_clarification", "unavailable", "failed"):
                raise AppError("request_closed", "This request already produced a proposal or found an existing tool; continue in that tool's review", 409)
            if not text and row["status"] != "failed":
                raise AppError("clarification_required", "Describe what the tool should do; only a failed request can be retried unchanged")
            items = json.loads(row["clarifications"]) + ([dict(text=text, by=self.settings.dev_reviewer_id, at=now())] if text else [])
            c.execute("UPDATE tool_requests SET clarifications=?,status='processing',updated_at=? WHERE id=?", (dump(items), now(), rid))
        return self.process_request(rid)

    def request_next_batch(self, rid):
        """Triage an unavailable request again against the operations earlier batches did not review."""
        with self.store.connect(write=True) as c:
            row = c.execute("SELECT status,operation_scope,coverage FROM tool_requests WHERE id=?", (rid,)).fetchone()
            if not row:
                raise AppError("not_found", "Record not found", 404)
            coverage = json.loads(row["coverage"]) if row["coverage"] else {}
            if row["status"] != "unavailable" or row["operation_scope"] is not None or not coverage.get("omitted_ids"):
                raise AppError("no_next_batch", "Only a request found unavailable while some operations were not yet reviewed can look in the next batch", 409)
            c.execute("UPDATE tool_requests SET status='processing',updated_at=? WHERE id=?", (now(), rid))
        return self.process_request(rid, coverage.get("all_considered_ids", coverage.get("considered_ids", [])))

    def suggestion(self, sid):
        row = self.store.one("SELECT * FROM suggestions WHERE id=?", (sid,))
        row["content"] = json.loads(row["content"])
        return row

    def suggestions(self, business_id):
        batches = self.store.all("SELECT * FROM suggestion_batches WHERE business_id=? ORDER BY created_at DESC", (business_id,))
        for b in batches:
            for key in ("coverage", "withheld", "recognized"):
                b[key] = json.loads(b[key])
        items = [dict(r, content=json.loads(r["content"])) for r in self.store.all("SELECT * FROM suggestions WHERE business_id=? ORDER BY created_at", (business_id,))]
        return dict(batches=batches, suggestions=items)

    def suggest(self, business_id, submission):
        """Ask the model for a few additional tools; deterministic screening decides category, grounding and duplicates."""
        spec = self.request_spec(business_id, submission.spec_id)
        count = max(1, min(submission.count or self.settings.suggestion_count, self.settings.suggestion_max))
        business = self.store.one("SELECT * FROM businesses WHERE id=?", (business_id,))
        existing = self.existing_tools(business_id)
        prior = [dict(r["content"], status=r["status"], category=r["category"]) for r in self.suggestions(business_id)["suggestions"]]
        goals = [r["goal"] for r in self.store.all("SELECT goal FROM tool_requests WHERE business_id=? ORDER BY created_at DESC LIMIT 10", (business_id,))]
        exclude = []
        if submission.next_batch:
            latest = self.store.all("SELECT coverage FROM suggestion_batches WHERE business_id=? AND spec_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (business_id, spec["id"]))
            if latest:
                previous = json.loads(latest[0]["coverage"])
                exclude = previous.get("all_considered_ids", previous.get("considered_ids", []))
        build = lambda index, coverage: dict(business=business, owner_goals=goals, max_suggestions=count, operation_index=dict(operations=index, coverage=capabilities.model_coverage(coverage)),
                                             existing_tools=existing, earlier_suggestions=[dict(title=p["title"], category=p["category"], status=p["status"], operation_ids=p["operation_ids"]) for p in prior])
        index, coverage, payload = self.fitted_index("suggestion", build, spec["inventory"], self.area_scope(spec["id"]), exclude)
        if not index:
            raise AppError("no_next_batch", "Every operation in the selected areas has already been reviewed; press Suggest additional tools to start again", 409)
        coverage["all_considered_ids"] = list(dict.fromkeys(list(exclude) + coverage["considered_ids"]))
        run, output = self.model_run(business_id, "suggestion", payload, SuggestionOutput)
        try:
            kept, withheld, recognized = capabilities.screen(output, index, existing, prior, count)
            bid, stamp = uid(), now()
            with self.store.connect(write=True) as c:
                c.execute("INSERT INTO suggestion_batches VALUES(?,?,?,?,?,?,?,?,?)", (bid, business_id, spec["id"], run, count, dump(coverage), dump(withheld), dump(recognized), stamp))
                for d in kept:
                    c.execute("INSERT INTO suggestions(id,batch_id,business_id,spec_id,content,category,status,created_at) VALUES(?,?,?,?,?,?,'open',?)",
                              (uid(), bid, business_id, spec["id"], dump(d), d["category"], stamp))
            self.store.finish_run(run, dict(batch_id=bid, kept=len(kept), withheld=len(withheld), recognized=len(recognized)))
        except Exception as exc:
            self.fail_run(run, exc)
            raise
        batch = next(b for b in self.suggestions(business_id)["batches"] if b["id"] == bid)
        return dict(batch, suggestions=[dict(r, content=json.loads(r["content"])) for r in self.store.all("SELECT * FROM suggestions WHERE batch_id=? ORDER BY rowid", (bid,))])

    def decide_suggestion(self, sid, submission):
        """Accept starts a normal tool request (never build approval); dismiss and revise touch only the suggestion."""
        s = self.suggestion(sid)
        if submission.action == "accept" and s["status"] == "accepted":
            return dict(suggestion=s, request=self.tool_request(s["request_id"]))
        if s["status"] != "open":
            raise AppError("suggestion_closed", f'This suggestion is already {s["status"]}', 409)
        who, stamp, note = self.settings.dev_reviewer_id, now(), submission.note.strip()
        if submission.action == "dismiss":
            with self.store.connect(write=True) as c:
                c.execute("UPDATE suggestions SET status='dismissed',decided_by=?,decided_at=?,decision_note=? WHERE id=? AND status='open'", (who, stamp, note, sid))
            return dict(suggestion=self.suggestion(sid))
        if submission.action == "revise":
            title, purpose = (submission.title or "").strip(), (submission.purpose or "").strip()
            if not title and not purpose:
                raise AppError("revision_required", "Give a new title or purpose")
            content = dict(s["content"], title=title or s["content"]["title"], purpose=purpose or s["content"]["purpose"], revised_by_owner=True)
            new = uid()
            with self.store.connect(write=True) as c:
                if c.execute("UPDATE suggestions SET status='revised',decided_by=?,decided_at=?,decision_note=? WHERE id=? AND status='open'", (who, stamp, note, sid)).rowcount != 1:
                    raise AppError("suggestion_closed", "This suggestion changed; refresh", 409)
                c.execute("INSERT INTO suggestions(id,batch_id,business_id,spec_id,parent_id,content,category,status,created_at) VALUES(?,?,?,?,?,?,?,'open',?)",
                          (new, s["batch_id"], s["business_id"], s["spec_id"], sid, dump(content), s["category"], stamp))
            return dict(suggestion=self.suggestion(new), revised=self.suggestion(sid))
        if s["category"] == "blocked_by_missing_api":
            raise AppError("suggestion_blocked", "The API lacks what this suggestion needs; it cannot become a proposal until the operation exists", 409)
        clarification = submission.clarification.strip()
        if s["category"] == "needs_clarification" and not clarification:
            raise AppError("clarification_required", "Answer the missing information before accepting this suggestion")
        c0 = s["content"]
        goal = f'{c0["title"]}: {c0["purpose"]}'[:2000]
        request = ToolRequestSubmission(goal=goal.ljust(10, "."), examples=c0["benefit"][:4000], spec_id=s["spec_id"], idempotency_key="suggestion:" + sid)
        with self.store.connect(write=True) as c:
            if c.execute("UPDATE suggestions SET status='accepted',decided_by=?,decided_at=?,decision_note=? WHERE id=? AND status='open'", (who, stamp, note, sid)).rowcount != 1:
                raise AppError("suggestion_closed", "This suggestion changed; refresh", 409)
        try:
            result = self.request_tool(s["business_id"], request, "suggestion", sid, c0["operation_ids"], [clarification] if clarification else [])
        except AppError:
            with self.store.connect(write=True) as c:
                c.execute("UPDATE suggestions SET status='open',decided_by=NULL,decided_at=NULL,decision_note=NULL WHERE id=? AND request_id IS NULL", (sid,))
            raise
        with self.store.connect(write=True) as c:
            c.execute("UPDATE suggestions SET request_id=? WHERE id=?", (result["id"], sid))
        return dict(suggestion=self.suggestion(sid), request=result)
