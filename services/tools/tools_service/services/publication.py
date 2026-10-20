import json
from ..config import AppError
from ..models import SandboxRunSubmission
from ..persistence.util import uid, now, dump
from ..connectors import LIVE_DEMO, PRODUCTION
from ..executor import artifact_environment, destination, load_connectors
from .. import promotions as promotion_rules
from .. import publishing
from ..persistence.repositories import promotions as promotions_repo, publications as publications_repo


class Publication:
    """Publication of tested artifacts (sandbox or Live Demo) and the MCP tool calls it allows, re-checked on every list
    and call. No publication is a production release: production_ready and production_activation are always false."""

    def __init__(self, settings, store, providers, building):
        self.settings, self.store, self.providers = settings, store, providers
        self.building = building
        self.after_production_call = None  # set by Service: the promotion service's error-spike watch

    def publication(self, pub_id):
        row = publications_repo.get(self.store, pub_id)
        return self.publication_view(row)

    def publication_view(self, row):
        """The record plus what it means now. Separate facts, never merged into one flag:
        status (published/disabled: does the publication exist and is it switched on), effective_status (blocked when a
        re-check fails), execution_enabled (would a call run right now), environment (sandbox or live_demo: where calls
        go), production_ready / production_activation (always false: no production release process exists)."""
        row = dict(row, evidence=json.loads(row["evidence"]), production_activation=False, production_ready=False,
                   production_assessment="not_assessed")
        promo = promotions_repo.for_publication(self.store, row["id"]) if row["environment"] == PRODUCTION else None
        problem = None
        if row["status"] == "published":
            try:
                self.check_publication(row)
            except AppError as exc:
                problem = dict(code=exc.code, message=exc.message)
        row["effective_status"] = row["status"] if not problem else "blocked"
        row["problem"] = problem
        row["execution_enabled"] = row["status"] == "published" and problem is None
        if promo:  # production is true only for a promoted publication whose promotion is active and whose re-checks hold
            row.update(promotion_id=promo["id"], production_activation=promo["status"] == "active" and row["execution_enabled"],
                       production_ready=promo["status"] == "active" and row["execution_enabled"],
                       production_assessment="passed" if promo["status"] == "active" else "rolled_back")
        return row

    def publications(self, business_id=None, artifact_id=None):
        return [self.publication_view(r) for r in publications_repo.listing(self.store, business_id, artifact_id)]

    def artifact_guidance(self, aid):
        """Ordered, actionable next steps for this artifact, plus any reason it can never run."""
        a = self.building.artifact(aid)
        c = a["content"]
        connector = load_connectors(self.settings).get(c["connector"]["id"]) or {}
        identities = sorted(connector.get("identities") or {})
        published = any(p["status"] == "published" for p in self.publications(artifact_id=aid))
        steps = publishing.next_steps(c, a["tests"], a["executions"], identities, a.get("policy_tests"), published)
        return dict(steps=steps, remaining=[s for s in steps if not s["done"]],
                    unrunnable=publishing.unrunnable(c, identities))

    def publication_problems(self, aid):
        """Everything that would block publishing this exact artifact now (empty when it may be published)."""
        a = self.building.artifact(aid)
        c = a["content"]
        problems = []
        try:
            publishing.tool_schemas(c)
            _, _, _, decision = self.building.approval(c["proposal"]["id"], c["proposal"]["version"], c["proposal"]["content_sha256"])
            if decision["id"] != c["proposal"]["decision_id"]:
                raise AppError("approval_not_current", "The artifact was built under a different approval decision", 409)
            self.building.check_enforcement(c)
            if c["execution_blockers"]:
                raise AppError("access_enforcement_missing", "Execution is blocked until every access requirement has an enforceable mechanism", 409)
            connector = load_connectors(self.settings).get(c["connector"]["id"])
            if not connector:
                raise AppError("connector_unknown", "The artifact's connector is not configured", 404)
            destination(connector, c, self.settings)
        except AppError as exc:
            problems.append(f"{exc.code}: {exc.message}")
        if c.get("access_policy") and (not a.get("policy_tests") or not a["policy_tests"]["passed"]):
            problems.append("Generated policy security tests must pass for this artifact hash")
        missing, evidence = publishing.evidence(c, a["tests"], a["executions"])
        if c.get("access_policy") and not a["readiness"]["publish_ready"]:
            problems.extend(a["readiness"]["blockers"])
        if a.get("policy_tests"):
            evidence["policy_tests"] = a["policy_tests"]
        return a, problems + missing, evidence

    def publish(self, aid, submission):
        """Explicit publication of one exact, approved and tested artifact in the environment it was built for
        (sandbox or Live Demo); idempotent while active. Never a production release."""
        built_for = artifact_environment(self.building.artifact(aid)["content"])
        if built_for == PRODUCTION:
            raise AppError("use_promotion", "A production tool is published only by an admin's promotion of a tested sandbox or Live Demo tool", 409)
        if submission.environment != built_for:
            raise AppError("environment_mismatch", f"This artifact was built for {built_for}; it can only be published to {built_for}", 409)
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
        """Re-read on every list and call: still published, same artifact bytes, same environment, Live Demo still switched
        on, same approval, same enforcement, evidence intact. (The destination itself is re-checked by the executor.)"""
        row = publications_repo.get(self.store, pub["id"])
        if row["status"] != "published":
            raise AppError("publication_disabled", "This tool's publication was disabled", 409)
        a = self.building.artifact(row["artifact_id"])
        c = a["content"]
        if a["sha256"] != row["artifact_sha256"]:
            raise AppError("artifact_integrity", "The artifact differs from the one that was published", 409)
        if row["environment"] != artifact_environment(c):
            raise AppError("environment_mismatch", "The publication's environment differs from the artifact's", 409)
        if row["environment"] == LIVE_DEMO and not self.settings.live_demo_enabled:
            raise AppError("live_demo_disabled", "Live Demo execution is switched off (TOOLS_LIVE_DEMO_ENABLED)", 409)
        production = row["environment"] == PRODUCTION
        evidence_of = a
        if production:
            if not self.settings.production_enabled:
                raise AppError("production_disabled", "Production execution is switched off (TOOLS_PRODUCTION_ENABLED)", 409)
            promo = promotions_repo.for_publication(self.store, row["id"])
            if not promo or promo["status"] != "active" or promo["production_sha256"] != a["sha256"]:
                raise AppError("promotion_not_active", "This production tool's promotion is not active (rolled back, or it no longer matches the artifact)", 409)
            source = self.building.artifact(promo["source_artifact_id"])
            if source["sha256"] != promo["source_sha256"]:
                raise AppError("artifact_integrity", "The tested artifact differs from the one that was promoted", 409)
            differ = promotion_rules.differences(source["content"], c)
            if differ:
                raise AppError("publication_evidence", "The production artifact no longer matches the tested one (" + ", ".join(differ) + ")", 409)
            evidence_of = source
        publishing.tool_schemas(c)
        _, _, _, decision = self.building.approval(c["proposal"]["id"], c["proposal"]["version"], c["proposal"]["content_sha256"])
        if decision["id"] != row["decision_id"]:
            raise AppError("approval_not_current", "The approval recorded at publication is no longer current", 409)
        current = self.building.check_enforcement(c)
        if (current["id"] if current else None) != row["enforcement_config_id"]:
            raise AppError("enforcement_not_current", "The enforcement recorded at publication is no longer current", 409)
        ec = evidence_of["content"]
        problems, _ = publishing.evidence(ec, evidence_of["tests"], evidence_of["executions"])
        if ec.get("access_policy") and (not evidence_of.get("policy_tests") or not evidence_of["policy_tests"]["passed"]):
            problems.append("Generated policy tests are missing or failed")
        if ec.get("access_policy") and not evidence_of["readiness"]["publish_ready"]:
            problems.extend(evidence_of["readiness"]["blockers"])
        if production and c["execution_blockers"]:
            problems.extend(c["execution_blockers"])
        if problems:
            raise AppError("publication_evidence", "Sandbox test evidence for this artifact no longer holds: " + "; ".join(problems), 409)
        return a

    def published_tools(self, business_id, identity, production=False):
        """MCP tool definitions for publications that are valid right now. A normal server lists the sandbox and Live Demo tools its
        identity can use; a production server (started with --context-from-client) lists only production tools."""
        tools = []
        for row in publications_repo.published(self.store, business_id):
            if (row["environment"] == PRODUCTION) != production:
                continue
            try:
                c = self.check_publication(row)["content"]
            except AppError:
                continue
            if production or identity in ((load_connectors(self.settings).get(c["connector"]["id"]) or {}).get("identities") or {}):
                tools.append(publishing.tool(row, c))
        return tools

    def invoke_published(self, business_id, identity, name, arguments, context=None, idempotency_key=None, production=False):
        """One MCP tool call through the existing executor. A sandbox or Live Demo identity comes from the server process, never
        from arguments. A production call (production server only) carries the verified customer's trusted `context` and an
        optional `idempotency_key` from the calling server, never from arguments; the server's service credential is used."""
        pub = publications_repo.latest_for_tool(self.store, name, business_id)
        if not pub or pub["status"] != "published" or (pub["environment"] == PRODUCTION) != production:
            raise AppError("tool_not_published", "No published tool with this name is available to this server", 404)
        audit = dict(channel="mcp_stdio", publication_id=pub["id"], tool_name=name)
        if production:
            promo = promotions_repo.for_publication(self.store, pub["id"])
            audit["idempotency_steps"] = {s: h for s, h in json.loads(promo["idempotency"])["plan"].items() if h} if promo else {}
        try:
            # run_sandbox records it as mcp_live_demo / mcp_production when the artifact is a Live Demo / production one
            result = self.building.run_sandbox(pub["artifact_id"], SandboxRunSubmission(identity="service" if production else identity, arguments=arguments or {}), "mcp_sandbox", audit,
                                               lambda: self.check_publication(pub), service_context=context if production else None, idempotency_key=idempotency_key if production else None)
        except AppError as exc:
            if "execution_id" not in exc.details:
                raise
            return publishing.rejected(pub, exc)
        if production and self.after_production_call:
            self.after_production_call(pub, result["report"])
        return publishing.executed(pub, result)
