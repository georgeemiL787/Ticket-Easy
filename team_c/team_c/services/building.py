from ..persistence.repositories import policies as policy_repo
import json
from ..config import AppError
from ..grounding import validate_proposal
from ..models import ProposalContent, RevisionSubmission, SandboxRunSubmission
from ..persistence.recovery import INTERRUPTED, OWNER
from ..persistence.util import uid, now, dump, digest
from ..artifacts import compile_artifact, validate_enforcement
from ..executor import Run, credentials, destination, load_connectors, validate_arguments
from .. import repair as repairs
from ..persistence.repositories import enforcement as enforcement_repo, proposals as proposals_repo


class Building:
    """From an approved version to evidence: enforcement review, artifacts, sandbox runs and tests, and bounded model repair."""

    def __init__(self, settings, store, providers, discovery, proposals, review):
        self.settings, self.store, self.providers = settings, store, providers
        self.discovery, self.proposals, self.review = discovery, proposals, review
        self.execution_transport = None

    def approval(self, pid, version, content_sha256=None):
        """The approve_to_build decision for this exact, still-current version, or an error."""
        row = self.store.one("SELECT v.*,p.current_version,p.business_id FROM versions v JOIN proposals p ON p.id=v.proposal_id WHERE v.proposal_id=? AND v.version=?", (pid, version))
        decisions = self.store.all("SELECT * FROM decisions WHERE proposal_id=? AND version=? AND action='approve_to_build'", (pid, version))
        content = json.loads(row["content"])
        spec = self.discovery.spec(row["spec_id"])
        if row["current_version"] != version or row["state"] != "approved_to_build" or not decisions:
            raise AppError("approval_not_current", "Only the current version with an approve_to_build decision can be built or run; a changed proposal needs fresh approval", 409)
        snapshot = json.loads(decisions[0]["snapshot"])
        if digest(snapshot["content"]) != digest(content) or snapshot["checksum"] != spec["checksum"] or (content_sha256 and content_sha256 != digest(content)):
            raise AppError("approval_not_current", "The approved snapshot no longer matches this version or its specification", 409)
        policy = policy_repo.current(self.store, pid, version)
        if snapshot.get("policy_sha256") != (policy["sha256"] if policy else None):
            raise AppError("policy_not_current", "The structured policy differs from the approved snapshot", 409)
        return row, content, spec, decisions[0]

    def approved_context(self, pid, connector_id):
        view = self.proposals.view(pid)
        row, content, spec, decision = self.approval(pid, view["version"])
        connector = load_connectors(self.settings).get(connector_id)
        if not connector or connector.get("business_id") != row["business_id"]:
            raise AppError("connector_unknown", "No configured connector with this id serves this business", 404)
        if view.get("policy"):
            from .connectors import Connectors
            Connectors(self.settings, self.store, self.proposals, self.discovery).apply_policy_context(connector_id, row["business_id"], view["policy"]["content"])
            connector = load_connectors(self.settings)[connector_id]
        connector = dict(id=connector_id, base_url=connector["base_url"].rstrip("/"), context_fields=sorted(connector.get("context_fields") or []))
        return view, row, content, spec, decision, connector, view["derived"].get("generation_scope")

    def enforcement(self, eid):
        return enforcement_repo.get(self.store, eid)

    def current_enforcement(self, pid, version):
        return enforcement_repo.current(self.store, pid, version)

    def submit_enforcement(self, pid, submission):
        """Operator-proposed mechanisms, validated now and usable only after owner review."""
        view, row, content, spec, _, connector, scope = self.approved_context(pid, submission.connector_id)
        if view.get("policy"):
            raise AppError("policy_managed_enforcement", "Enforcement is generated from the accepted structured policy when building; a separate manual configuration is not needed", 409)
        validate_enforcement(submission.enforcement, content, self.proposals.scoped(spec["inventory"], scope), view["requirements"], connector)
        sha = digest(dict(connector=connector, enforcement=submission.enforcement))
        current = self.current_enforcement(pid, row["version"])
        if current and current["sha256"] == sha:
            return current
        eid = uid()
        with self.store.connect(write=True) as c:
            c.execute("INSERT INTO enforcement_configs(id,proposal_id,version,connector_id,content,sha256,submitted_by,submitted_at) VALUES(?,?,?,?,?,?,?,?)",
                      (eid, pid, row["version"], submission.connector_id, dump(dict(connector=connector, enforcement=submission.enforcement)), sha, self.settings.dev_reviewer_id, now()))
            # DEV_FAST_TRACK: auto-review enforcement on submission (no separate review step).
            if self.settings.dev_fast_track:
                c.execute("UPDATE enforcement_configs SET reviewed_by=?,reviewed_at=?,review_note=? WHERE id=? AND reviewed_at IS NULL",
                          (self.settings.dev_reviewer_id, now(), "Auto-reviewed (DEV_FAST_TRACK)", eid))
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

    def check_enforcement(self, content):
        current = self.current_enforcement(content["proposal"]["id"], content["proposal"]["version"])
        built = (content["enforcement_config"] or {}).get("id")
        if built != (current["id"] if current else None) or (current and current["status"] != "approved"):
            raise AppError("enforcement_not_current", "The enforcement configuration changed after this artifact was built; review it and rebuild", 409)
        policy = policy_repo.current(self.store, content["proposal"]["id"], content["proposal"]["version"])
        if (content.get("access_policy") or {}).get("sha256") != (policy["sha256"] if policy else None):
            raise AppError("policy_not_current", "The artifact must be rebuilt under the accepted policy", 409)
        # Old immutable artifacts are inspected under today's trust-boundary rules.
        row = self.proposals.view(content["proposal"]["id"], content["proposal"]["version"])
        from ..compiler.policies import resolve_inputs
        _, _, problems = resolve_inputs(row["content"], self.discovery.spec(row["spec_id"])["inventory"], policy["content"] if policy else None)
        if problems:
            raise AppError("semantic_inputs_unresolved", "Existing tool exposes unresolved protected inputs; revise and rebuild", 409, {"blockers": problems})
        return current

    def build_artifact(self, pid, submission):
        view, row, content, spec, decision, connector, scope = self.approved_context(pid, submission.connector_id)
        if view["interface_preview"]["blockers"]:
            names = ", ".join(view["interface_preview"]["blocked_arguments"])
            raise AppError("semantic_inputs_unresolved", "Accept trusted input mappings before building" + (": " + names if names else ""),
                           details=dict(blockers=view["interface_preview"]["blockers"], recovery_url=f"/proposals/{pid}#structured-policy"))
        fields = validate_proposal(ProposalContent.model_validate(content), spec["inventory"], scope)
        if fields["blockers"]:
            raise AppError("artifact_not_approved", "Grounding reports blockers: " + "; ".join(fields["blockers"]))
        config = self.current_enforcement(pid, row["version"])
        # DEV_FAST_TRACK: auto-create and auto-review enforcement if none exists.
        if not config and self.settings.dev_fast_track and view["requirements"]:
            enforcement_data = {r["id"]: {"mechanism": "delegated_user_credential"} for r in view["requirements"]}
            sha = digest(dict(connector=connector, enforcement=enforcement_data))
            eid = uid()
            with self.store.connect(write=True) as c:
                c.execute("INSERT INTO enforcement_configs(id,proposal_id,version,connector_id,content,sha256,submitted_by,submitted_at,reviewed_by,reviewed_at,review_note) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                          (eid, pid, row["version"], submission.connector_id, dump(dict(connector=connector, enforcement=enforcement_data)), sha,
                           self.settings.dev_reviewer_id, now(), self.settings.dev_reviewer_id, now(), "Auto-generated and reviewed (DEV_FAST_TRACK)"))
            config = self.current_enforcement(pid, row["version"])
        if config and config["content"]["connector"] != connector:
            raise AppError("enforcement_connector", "The reviewed enforcement was configured for a different connector or connector context; submit it again for review", 409)
        if config and config["status"] != "approved":
            raise AppError("enforcement_unreviewed", "The current enforcement configuration awaits owner review", 409)
        proposal = dict(id=pid, version=row["version"], business_id=row["business_id"], content_sha256=digest(content), decision_id=decision["id"],
                        decided_at=decision["created_at"], reviewer=decision["reviewer"], reconciliation_id=row["reconciliation_id"])
        enforcement = dict(config, content=config["content"]["enforcement"]) if config else None
        artifact, sha = compile_artifact(content, fields, self.proposals.scoped(spec["inventory"], scope), view["requirements"], proposal,
                                         dict(spec_id=spec["id"], spec_sha256=spec["checksum"]), connector, enforcement, policy_repo.current(self.store, pid, row["version"]))
        with self.store.connect(write=True) as c:
            existing = c.execute("SELECT id FROM artifacts WHERE proposal_id=? AND version=? AND sha256=?", (pid, row["version"], sha)).fetchone()
            aid = existing["id"] if existing else uid()
            if not existing:
                c.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)", (aid, pid, row["version"], decision["id"], sha, dump(artifact), now()))
        built = self.artifact(aid)
        if artifact.get("access_policy"):
            from .capability import Capability
            Capability(self.settings, self.store, self.proposals, self.discovery).tests(built)
            built = self.artifact(aid)
        return built

    def artifact(self, aid):
        row = self.store.one("SELECT * FROM artifacts WHERE id=?", (aid,))
        row["content"] = json.loads(row["content"])
        if digest(row["content"]) != row["sha256"]:
            raise AppError("artifact_integrity", "Stored artifact content does not match its hash", 409)
        row["executions"] = [dict(e, report=json.loads(e["report"]) if e["report"] else None) for e in self.store.all("SELECT * FROM executions WHERE artifact_id=? ORDER BY created_at", (aid,))]
        row["tests"] = [dict(t, expectation=json.loads(t["expectation"]), failure_report=json.loads(t["failure_report"]) if t["failure_report"] else None) for t in self.store.all("SELECT * FROM sandbox_tests WHERE artifact_id=? ORDER BY created_at", (aid,))]
        # Evidence rules keep every run for audit but only the latest run of each named test counts,
        # so mark which rows publication would actually use (the last row of a name, as evidence() reads them).
        latest = {t["name"]: t["id"] for t in row["tests"]}
        for t in row["tests"]:
            t["current"] = latest[t["name"]] == t["id"]
        tests = self.store.all("SELECT report FROM policy_test_runs WHERE artifact_id=? AND artifact_sha256=? ORDER BY created_at DESC LIMIT 1", (aid, row["sha256"]))
        row["policy_tests"] = json.loads(tests[0]["report"]) if tests else None
        if row["content"].get("access_policy"):
            from ..compiler.readiness import assess
            version = self.proposals.view(row["proposal_id"], row["version"])
            row["readiness"] = assess(version["content"], self.discovery.spec(version["spec_id"])["inventory"], row["content"]["access_policy"], row["content"], row["policy_tests"])
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
        content = proposals_repo.version_content(self.store, a["proposal_id"], a["version"])
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
        view = self.proposals.view(a["proposal_id"], a["version"])
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
        view = self.proposals.view(pid)
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
        spec = self.discovery.spec(view["spec_id"])
        contract = repairs.evidence(report, view["content"], {o["id"]: o for o in spec["inventory"]["operations"]})
        try:
            response = self.review.revise(pid, RevisionSubmission(expected_revision=view["review_revision"], instruction=repairs.instruction(report, contract, history)), repairs.check_scope, "automated_repair", repair=True)
        except AppError as exc:
            exc.details["repair_id"] = record(exc.code, repairs.feedback(exc, report), exc.details.get("run_id"))
            raise
        if response.get("repair_outcome"):
            label = dict(capability_gap="Model reported a missing capability; owner clarification or a new operation is required: ",
                         cannot_repair="Model reported that rewiring the existing steps cannot meet the expected behavior; owner revision is required: ")[response["repair_outcome"]]
            detail = label + response["explanation"]
            return dict(outcome=response["repair_outcome"], repair_id=record(response["repair_outcome"], detail, response["run_id"]), detail=detail, attempt=attempts + 1)
        changes = self.proposals.view(pid)["change_summary"]
        rid = record("revised", f'Version {response["version"]} created (changes: {"; ".join(changes)}); it needs fresh answers, reconciliation, confirmation, approval, enforcement review and a rebuild before the test is rerun', response["run_id"], response["version"])
        return dict(outcome="revised", repair_id=rid, attempt=attempts + 1, version=response["version"], state=response["state"], changes=changes)
