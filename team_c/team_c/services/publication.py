import json
from ..config import AppError
from ..models import SandboxRunSubmission
from ..persistence.util import uid, now, dump
from ..executor import destination, load_connectors
from .. import publishing
from ..persistence.repositories import publications as publications_repo


class Publication:
    """TEST-ONLY sandbox publication of tested artifacts and the MCP tool calls it allows, re-checked on every list and call."""

    def __init__(self, settings, store, providers, building):
        self.settings, self.store, self.providers = settings, store, providers
        self.building = building

    def publication(self, pub_id):
        row = publications_repo.get(self.store, pub_id)
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
        return [self.publication_view(r) for r in publications_repo.listing(self.store, business_id, artifact_id)]

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
        row = publications_repo.get(self.store, pub["id"])
        if row["status"] != "published":
            raise AppError("publication_disabled", "This tool's sandbox publication was disabled", 409)
        a = self.building.artifact(row["artifact_id"])
        c = a["content"]
        if a["sha256"] != row["artifact_sha256"]:
            raise AppError("artifact_integrity", "The artifact differs from the one that was published", 409)
        publishing.tool_schemas(c)
        _, _, _, decision = self.building.approval(c["proposal"]["id"], c["proposal"]["version"], c["proposal"]["content_sha256"])
        if decision["id"] != row["decision_id"]:
            raise AppError("approval_not_current", "The approval recorded at publication is no longer current", 409)
        current = self.building.check_enforcement(c)
        if (current["id"] if current else None) != row["enforcement_config_id"]:
            raise AppError("enforcement_not_current", "The enforcement recorded at publication is no longer current", 409)
        problems, _ = publishing.evidence(c, a["tests"], a["executions"])
        if c.get("access_policy") and (not a.get("policy_tests") or not a["policy_tests"]["passed"]):
            problems.append("Generated policy tests are missing or failed")
        if c.get("access_policy") and not a["readiness"]["publish_ready"]:
            problems.extend(a["readiness"]["blockers"])
        if problems:
            raise AppError("publication_evidence", "Sandbox test evidence for this artifact no longer holds: " + "; ".join(problems), 409)
        return a

    def published_tools(self, business_id, identity):
        """MCP tool definitions for publications that are valid right now and usable by this server's identity."""
        tools = []
        for row in publications_repo.published(self.store, business_id):
            try:
                c = self.check_publication(row)["content"]
            except AppError:
                continue
            if identity in ((load_connectors(self.settings).get(c["connector"]["id"]) or {}).get("identities") or {}):
                tools.append(publishing.tool(row, c))
        return tools

    def invoke_published(self, business_id, identity, name, arguments):
        """One MCP tool call through the existing executor; identity comes from the server process, never from arguments."""
        pub = publications_repo.latest_for_tool(self.store, name, business_id)
        if not pub or pub["status"] != "published":
            raise AppError("tool_not_published", "No sandbox-published tool with this name is available to this server", 404)
        audit = dict(channel="mcp_stdio", publication_id=pub["id"], tool_name=name)
        try:
            result = self.building.run_sandbox(pub["artifact_id"], SandboxRunSubmission(identity=identity, arguments=arguments or {}), "mcp_sandbox", audit, lambda: self.check_publication(pub))
        except AppError as exc:
            if "execution_id" not in exc.details:
                raise
            return publishing.rejected(pub, exc)
        return publishing.executed(pub, result)
