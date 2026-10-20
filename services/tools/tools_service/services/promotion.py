"""Promotion of a sandbox- or Live Demo-tested tool to production, its rollback, and the automatic disable on an error spike.

Promotion is a human decision (a Team B admin, recorded with name, time and note) with automated checks. Nothing here makes a
tool production by itself: every item of the readiness checklist must pass, each is stored with its evidence, and a refused
attempt is kept too. A production publication then stays valid only while its promotion is active and everything it was
promoted on still holds (re-checked on every list and call by Publication.check_publication).
"""
import json
from datetime import datetime, timedelta, timezone

from .. import autotest as plans
from .. import promotions as rules
from .. import publishing
from ..config import AppError
from ..connectors import PRODUCTION, environment_of, load_connectors, resolve_secret
from ..executor import artifact_environment
from ..models import ArtifactBuildSubmission, SandboxRunSubmission
from ..persistence.repositories import promotions as promotions_repo
from ..persistence.util import dump, now, uid

CHECKLIST = ("source_published", "approval_current", "enforcement_reviewed", "sandbox_evidence", "service_scoped", "content_identical",
             "secret_resolved", "idempotency", "rate_limits", "rollback_contact", "smoke")
BAD_OUTCOMES = {"api_unavailable", "api_unreachable", "timeout", "invalid_response", "partial_write", "unknown_outcome", "authentication_failed", "internal_error"}


class Promotion:
    def __init__(self, settings, store, building, publishing_service, connectors, discovery):
        self.settings, self.store = settings, store
        self.building, self.publishing, self.connectors, self.discovery = building, publishing_service, connectors, discovery

    # ---- reading ----

    @staticmethod
    def view(row):
        return dict(row, checklist=json.loads(row["checklist"]), idempotency=json.loads(row["idempotency"]))

    def promotion(self, pid):
        row = promotions_repo.get(self.store, pid)
        if not row:
            raise AppError("promotion_not_found", "No promotion with this id", 404)
        return self.view(row)

    def promotions(self, business_id=None, artifact_id=None):
        return [self.view(r) for r in promotions_repo.listing(self.store, business_id, artifact_id)]

    def production_connector(self, business_id):
        """The business's single production connector (more than one needs an explicit id)."""
        found = [cid for cid, c in load_connectors(self.settings).items() if c.get("business_id") == business_id and environment_of(c) == PRODUCTION]
        if len(found) != 1:
            raise AppError("production_connector", "Name the production connector: this business has " + (f"{len(found)} of them" if found else "none; save one first"), 409)
        return found[0]

    # ---- promotion ----

    def promote(self, source_aid, connector_id, actor, note, rollback_contact, accept_no_idempotency=False):
        if not self.settings.production_enabled:
            raise AppError("production_disabled", "Production is switched off (PRODUCTION_ENABLED); nothing was promoted", 403)
        source = self.building.artifact(source_aid)
        sc = source["content"]
        if artifact_environment(sc) == PRODUCTION:
            raise AppError("promotion_source", "Promote a sandbox or Live Demo artifact; this one is already a production artifact", 409)
        business_id, pid = sc["proposal"]["business_id"], sc["proposal"]["id"]
        connector_id = connector_id or self.production_connector(business_id)
        connector = load_connectors(self.settings).get(connector_id)
        if not connector or connector.get("business_id") != business_id or environment_of(connector) != PRODUCTION:
            raise AppError("production_connector", "Name a production connector of this business", 404)
        if not (note or "").strip() or len((actor or "").strip()) < 2:
            raise AppError("promotion_attribution", "A promotion records who decided it and why: an actor and a note are required")
        items = {}

        def item(key, ok, detail, **more):
            items[key] = dict(ok=bool(ok), detail=detail, **more)

        pubs = [p for p in self.publishing.publications(artifact_id=source_aid) if p["status"] == "published" and p["execution_enabled"]]
        item("source_published", pubs, f"published to the {pubs[0]['environment']} MCP" if pubs else "the source artifact is not published and executable right now")
        try:
            self.building.approval(pid, sc["proposal"]["version"], sc["proposal"]["content_sha256"])
            item("approval_current", True, "the approved version and its structured policy are current")
        except AppError as exc:
            item("approval_current", False, exc.message)
        try:
            self.building.check_enforcement(sc)
            item("enforcement_reviewed", True, "enforcement is current and reviewed")
        except AppError as exc:
            item("enforcement_reviewed", False, exc.message)
        _, problems, evidence = self.publishing.publication_problems(source_aid)
        item("sandbox_evidence", not problems, "; ".join(problems) or f"{len(evidence['test_ids'])} passing tests (required: {', '.join(evidence['required'])})")
        name = connector.get("secret_ref")
        item("secret_resolved", resolve_secret(name) is not None, f"{name} is " + ("available" if resolve_secret(name) is not None else "NOT available to Team C"))
        item("rate_limits", self.settings.production_calls_per_minute > 0 and self.settings.production_calls_per_customer_per_minute > 0,
             f"{self.settings.production_calls_per_minute} calls/min per connector, {self.settings.production_calls_per_customer_per_minute} per customer")
        item("rollback_contact", len((rollback_contact or "").strip()) >= 3, f"rollback contact: {(rollback_contact or '').strip() or 'none named'}")

        prod = None
        if items["approval_current"]["ok"] and items["enforcement_reviewed"]["ok"]:
            try:
                prod = self.building.build_artifact(pid, ArtifactBuildSubmission(connector_id=connector_id))
            except AppError as exc:
                item("service_scoped", False, f"the production artifact could not be built: {exc.message}")
        idem = {}
        if prod is not None and any(r["status"] == "active" for r in promotions_repo.listing(self.store, business_id, prod["id"])):
            raise AppError("already_promoted", "This tool is already promoted to production; roll that promotion back first if it must be promoted again", 409)
        if prod is not None:
            pc = prod["content"]
            blockers = pc["execution_blockers"]
            item("service_scoped", not blockers, "; ".join(blockers) or "every record-scope requirement is a Team C check against the verified customer's context; no unrestricted, API-filtered or list scope")
            differ = rules.differences(sc, pc)
            item("content_identical", not differ, ("differs from the tested artifact in: " + ", ".join(differ)) if differ else "identical to the tested artifact except destination and activation")
            ops = self.discovery.spec(self.building.proposals.view(pid, source["version"])["spec_id"])["inventory"]["operations"]
            idem = rules.idempotency_plan(pc, ops)
            missing = sorted(s for s, h in idem.items() if not h)
            ok = not missing or accept_no_idempotency
            item("idempotency", ok, ("writes use the API's idempotency header: " + ", ".join(f"{s}: {h}" for s, h in idem.items() if h) if idem and not missing
                                     else "no writes" if not idem
                                     else ("owner accepted NO idempotency for " + ", ".join(missing) + " (a retried write could be applied twice; uncertain outcomes stay uncertain)"
                                           if accept_no_idempotency else "no idempotency mechanism for " + ", ".join(missing) + ": an admin must explicitly accept this")),
                 accepted_without=missing if accept_no_idempotency else [])
        else:
            for key in ("service_scoped", "content_identical", "idempotency"):
                items.setdefault(key, dict(ok=False, detail="not checked: the production artifact could not be built"))
        if all(v["ok"] for k, v in items.items() if k != "smoke") and prod is not None:
            self.smoke(prod, connector_id, items)
        else:
            items["smoke"] = dict(ok=False, detail="not run: the checks above must pass first; production is not touched before then")
        ordered = {k: items[k] for k in CHECKLIST}
        passed = all(v["ok"] for v in ordered.values())
        pid_new = uid()
        row = dict(id=pid_new, business_id=business_id, source_artifact_id=source_aid, production_artifact_id=prod["id"] if prod else None,
                   production_connector_id=connector_id, publication_id=None, actor=actor.strip(), note=note.strip(), rollback_contact=(rollback_contact or "").strip(),
                   checklist=dump(ordered), idempotency=dump(dict(plan=idem, accepted_without=ordered["idempotency"].get("accepted_without", []))), source_sha256=source["sha256"],
                   production_sha256=prod["sha256"] if prod else None, status="active" if passed else "refused", created_at=now())
        with self.store.connect(write=True) as c:
            if passed:
                pc = prod["content"]
                enforcement = pc["enforcement_config"] or {}
                active = c.execute("SELECT id FROM publications WHERE artifact_id=? AND status='published'", (prod["id"],)).fetchone()
                pub_id = active["id"] if active else uid()
                row["publication_id"] = pub_id
                if not active:
                    c.execute("INSERT INTO publications(id,artifact_id,proposal_id,version,business_id,artifact_sha256,decision_id,enforcement_config_id,enforcement_sha256,evidence,environment,tool_name,note,publisher,published_at,status) "
                              "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'published')",
                              (pub_id, prod["id"], prod["proposal_id"], prod["version"], business_id, prod["sha256"], pc["proposal"]["decision_id"], enforcement.get("id"),
                               enforcement.get("sha256"), dump(dict(promotion_id=pid_new, source_artifact_id=source_aid, checklist={k: v["ok"] for k, v in ordered.items()},
                                                                   required=evidence["required"], test_ids=evidence["test_ids"])),
                               "production", publishing.tool_name(prod["id"], pc), note.strip(), actor.strip(), now()))
            c.execute("INSERT INTO promotions(id,business_id,source_artifact_id,production_artifact_id,production_connector_id,publication_id,actor,note,rollback_contact,checklist,idempotency,source_sha256,production_sha256,status,created_at) "
                      "VALUES(:id,:business_id,:source_artifact_id,:production_artifact_id,:production_connector_id,:publication_id,:actor,:note,:rollback_contact,:checklist,:idempotency,:source_sha256,:production_sha256,:status,:created_at)", row)
        if not passed:
            failed = [f"{k}: {v['detail']}" for k, v in ordered.items() if not v["ok"]]
            raise AppError("promotion_refused", "This tool cannot be promoted yet: " + "; ".join(failed), 409, dict(promotion_id=pid_new, checklist=ordered))
        return self.promotion(pid_new)

    def smoke(self, prod, connector_id, items):
        """Production smoke check: the connection check plus the tool's read steps before its first write, for the operator's
        internal test customer. A write is never run in production by a promotion."""
        try:
            context = json.loads(self.settings.production_smoke_context or "null")
            arguments = json.loads(self.settings.production_smoke_arguments or "null")
        except ValueError:
            context = arguments = None
        if not isinstance(context, dict) or not context or not isinstance(arguments, dict):
            items["smoke"] = dict(ok=False, detail="PRODUCTION_SMOKE_CONTEXT and PRODUCTION_SMOKE_ARGUMENTS (JSON objects naming an internal test customer and that customer's own record) are not configured")
            return
        schema = prod["content"]["input_schema"]
        properties = schema.get("properties") or {}
        # PRODUCTION_SMOKE_ARGUMENTS names the internal test customer's record (it may name inputs this tool does not have: those are
        # ignored); every other required input is filled from the tool's own schema, as the automatic tests do.
        arguments = {k: v for k, v in arguments.items() if k in properties}
        unresolved = []
        for name in schema.get("required") or []:
            if name not in arguments:
                value, source = plans.schema_value(name, properties.get(name))
                if source == "unresolved":
                    unresolved.append(name)
                else:
                    arguments[name] = value
        if unresolved:
            items["smoke"] = dict(ok=False, detail="PRODUCTION_SMOKE_ARGUMENTS must also give: " + ", ".join(unresolved))
            return
        try:
            check = self.connectors.check(connector_id, [], [], transport=self.building.execution_transport)
            reached = [c for c in check["checks"] if c["check"] in ("configuration", "reachable", "secret", "authentication")]
            if any(c["status"] == "failed" for c in reached):
                items["smoke"] = dict(ok=False, detail="connection check failed: " + "; ".join(f"{c['check']}: {c['detail']}" for c in reached if c["status"] == "failed"))
                return
            result = self.building.run_sandbox(prod["id"], SandboxRunSubmission(identity="service", arguments=arguments), mode="production_smoke",
                                               audit=dict(channel="promotion_smoke"), precheck=lambda: None, service_context=context, smoke=True)
        except AppError as exc:
            items["smoke"] = dict(ok=False, detail=f"{exc.code}: {exc.message}")
            return
        status = result["report"]["status"]
        reads = [e["step_id"] for e in result["report"]["trace"] if e["effect"] == "read" and e.get("outcome") == "succeeded"]
        items["smoke"] = dict(ok=status == "succeeded", detail=f"connection check passed; read steps {', '.join(reads) or 'none'} ran as the smoke customer: {status}", execution_id=result["execution_id"])

    # ---- rollback and automatic disable ----

    def rollback(self, promotion_id, actor, note, auto=False):
        """Disable the production publication of a promotion; sandbox and Live Demo publications are left as they are. Blocks every
        later call; it cannot undo a write that already completed or is in flight."""
        promo = self.promotion(promotion_id)
        if promo["status"] != "active":
            raise AppError("promotion_not_active", f"This promotion is {promo['status']}", 409)
        if not (note or "").strip():
            raise AppError("reason_required", "Say why production is being rolled back; the reason is kept")
        with self.store.connect(write=True) as c:
            c.execute("UPDATE publications SET status='disabled',disabled_by=?,disabled_at=?,disable_note=? WHERE id=? AND status='published'",
                      (actor, now(), note.strip(), promo["publication_id"]))
            c.execute("UPDATE promotions SET status='rolled_back',rolled_back_by=?,rolled_back_at=?,rollback_note=? WHERE id=?", (actor, now(), note.strip(), promotion_id))
        return self.promotion(promotion_id)

    def after_call(self, pub, report):
        """Called after every production execution: when the tool's recent calls show the API (or our link to it) failing, roll the
        tool back by itself. A customer who asks for someone else's record is refused by a tools service check and never counts: only
        outcomes that point at the API or the integration do (unavailable, timeout, bad response, partial or unknown writes, bad login)."""
        if report["status"] == "succeeded":
            return None
        failure = publishing.failure_category(report) if report["status"] != "rejected" else None
        if failure not in BAD_OUTCOMES:
            return None
        promo = promotions_repo.for_publication(self.store, pub["id"])
        if not promo or promo["status"] != "active":
            return None
        since = (datetime.now(timezone.utc) - timedelta(seconds=self.settings.production_auto_disable_window_seconds)).isoformat()
        rows = self.store.all("SELECT report FROM executions WHERE artifact_id=? AND mode LIKE '%production%' AND status IN ('failed','partial','outcome_unknown') AND created_at>=?", (pub["artifact_id"], since))
        bad = sum(1 for r in rows if publishing.failure_category(json.loads(r["report"])) in BAD_OUTCOMES)
        if bad >= self.settings.production_auto_disable_failures:
            return self.rollback(promo["id"], "auto-disable", f"{bad} failing production calls (API or integration errors) within {self.settings.production_auto_disable_window_seconds} s; switched off automatically", auto=True)
        return None
