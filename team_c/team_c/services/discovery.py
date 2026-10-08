from ..compiler.semantics import enrich
import hashlib
import ipaddress
import json
import re
import threading
from pathlib import Path
from urllib.parse import urljoin, urlparse
import httpx
from ..config import AppError
from ..discovery import discover
from ..models import CodeAnalysisOutput, AreaNamingOutput, AreaAssignmentOutput
from ..persistence.util import uid, now, dump, digest
from ..llm.budget import input_fits
from .. import capabilities
from .. import areas as business_areas


class Discovery:
    """Businesses, their API descriptions (uploaded, fetched or legacy local code) and the business areas of each description."""

    def __init__(self, settings, store, providers, runs):
        self.settings, self.store, self.providers = settings, store, providers
        self.runs = runs
        self.organizing = threading.Lock()
        self.fetch_transport = None

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
        hostname = parsed.hostname.lower().rstrip(".")
        try:
            loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            loopback = hostname == "localhost"
        if host not in allowed and not ("loopback:*" in allowed and loopback):
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
        inventory = enrich(inventory)
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
        row["inventory"] = enrich(inventory)
        return row

    def local_project(self, business_id, path, setting_values=None):
        from ..legacy.code_discovery import index_project, discover_project, validate_setting_values
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
        from ..llm.prompts import CODE_SYSTEM
        spec=self.spec(spec_id);inventory=spec["inventory"]
        if inventory.get("source_kind")=="code":
            raise AppError("legacy_code_inventory","Local-code inventories are retained read-only; code analysis is retired",409)
        if inventory.get("source_kind")!="code" or not inventory["valid"]:
            raise AppError("code_analysis_blocked","Code analysis requires at least one supported code operation")
        business=self.store.one("SELECT * FROM businesses WHERE id=?",(spec["business_id"],))
        self.providers.configured_chain() if hasattr(self.providers,"configured_chain") else None
        provider_config={k:getattr(self.settings,k) for k in ("llm_primary","llm_fallback","ollama_model","ollama_base_url","ollama_think","ollama_context","groq_model","groq_base_url","groq_reasoning_effort")}
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
        run,result=self.runs.model_run(spec["business_id"],"code_analysis",payload,CodeAnalysisOutput)
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
            self.runs.fail_run(run,exc);raise

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
        index, coverage, _ = self.runs.fitted_index("request_triage", lambda index, coverage: dict(operation_index=dict(operations=index, coverage=capabilities.model_coverage(coverage))),
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

    def _organize_areas(self, spec_id, spec):
        """Two small steps an 8B model handles: name and rank the areas, then assign groups in batches."""
        groups = business_areas.base_groups(spec["inventory"])
        b = self.store.one("SELECT * FROM businesses WHERE id=?", (spec["business_id"],))
        business = dict(name=b["name"], description=b["description"])
        run = None
        try:
            run, named = self.runs.checked_run(spec["business_id"], "area_naming", dict(business=business, groups=business_areas.model_groups(groups, 0)),
                                               AreaNamingOutput, business_areas.named_areas)
            areas = [dict(name=a["name"], description=a["description"]) for a in named]
            assigned = {}
            for start in range(0, len(groups), business_areas.ASSIGN_BATCH):
                batch = groups[start:start + business_areas.ASSIGN_BATCH]
                for samples in (3, 1, 0):
                    payload = dict(business=business, areas=areas, groups=business_areas.model_groups(batch, samples))
                    if input_fits(self.settings, "area_assignment", payload):
                        break
                _, part = self.runs.checked_run(spec["business_id"], "area_assignment", payload, AreaAssignmentOutput,
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
