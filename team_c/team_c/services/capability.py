"""Owner decisions and compiler evidence, persisted independently of model output."""
from .. import requirements
from ..config import AppError
from ..compiler.policies import resolve_inputs
from ..compiler.security_tests import run
from ..persistence.util import uid, now, dump, digest
from ..persistence.repositories import proposals as proposal_repo
from ..models import ProposalContent


class Capability:
    def __init__(self, settings, store, proposals, discovery):
        self.settings, self.store, self.proposals, self.discovery = settings, store, proposals, discovery

    def reopen(self, pid, version, submission):
        """Open policy review without asking a model to rewrite approved operations."""
        view = self.proposals.view(pid, version)
        draft = view["policy"]["content"] if view["policy"] else view["derived"].get("policy_draft", {})
        enforcement = view.get("enforcement")
        if not draft and enforcement and enforcement["status"] == "approved":
            configs = enforcement["content"]["enforcement"].values()
            if any(c.get("mechanism") == "delegated_user_credential" for c in configs):
                draft = dict(principals=["end_user"], authentication="required")
        fields = dict(view["derived"], policy_review_required=True,
                      policy_draft=draft)
        run = uid()
        with self.store.connect(write=True) as c:
            row = self.proposals.check_current(c, pid, version, submission.expected_revision, True)
            if row["state"] not in {"approved_to_build", "rejected", "changes_requested"}:
                raise AppError("policy_review_open", "This version already has an editable policy form", 409)
            c.execute("INSERT INTO runs(id,business_id,kind,input_hash,status,created_at) VALUES(?,?,?,?,?,?)",
                      (run, view["business_id"], "policy_revision", digest(dict(proposal_id=pid, version=version)), "running", now()))
            new_version = proposal_repo.replace_version(c, pid, version, view["spec_id"], view["content"], fields, run, "needs_clarification")
            proposal_repo.copy_answers(c, pid, new_version, view["answers"].values())
            state = self.proposals.settle(c, pid, new_version, ProposalContent.model_validate(view["content"]), fields)
            result = dict(version=new_version, state=state, note="Policy review reopened; operations and answers preserved; renewed policy acceptance and approval required")
            c.execute("UPDATE runs SET status='succeeded',result=?,completed_at=? WHERE id=?", (dump(result), now(), run))
        return self.proposals.view(pid, new_version)

    def accept(self, pid, version, submission):
        view = self.proposals.view(pid, version)
        policy = submission.policy.model_dump()
        inventory = self.discovery.spec(view["spec_id"])["inventory"]
        effective, _, problems = resolve_inputs(view["content"], inventory, policy)
        known = {r["id"]: r for r in view["requirements"]}
        scoped = {c["requirement_id"] for c in policy["checks"] + policy["scopes"]}
        if scoped - set(known):
            raise AppError("policy_scope", "A policy decision refers to an unknown requirement")
        if any(known[r]["kind"] != "record_scope" for r in scoped):
            raise AppError("policy_scope", "Scope decisions must refer to resource-scope requirements")
        if problems:
            raise AppError("policy_inputs_unresolved", "Resolve the input decisions before accepting this policy", details={"blockers": problems})
        missing = [r["id"] for r in known.values() if r["kind"] == "record_scope" and r["id"] not in scoped]
        if missing:
            raise AppError("policy_scope_unresolved", "Record scopes require a checked or unrestricted decision", details={"requirements": missing})
        pid_policy = uid()
        with self.store.connect(write=True) as c:
            self.proposals.check_current(c, pid, version, submission.expected_revision)
            c.execute("INSERT INTO capability_policies(id,proposal_id,version,content,sha256,accepted_by,created_at) VALUES(?,?,?,?,?,?,?)",
                      (pid_policy, pid, version, dump(policy), digest(policy), self.settings.dev_reviewer_id, now()))
            for r in requirements.ensure(c, pid, version):
                answer = c.execute("INSERT INTO answers(proposal_id,version,question_id,text,reviewer,created_at) VALUES(?,?,?,?,?,?)",
                                   (pid, version, r["id"], dump(dict(policy_id=pid_policy, policy_sha256=digest(policy), decision=policy)), self.settings.dev_reviewer_id, now()))
                requirements.event(c, pid, version, r["id"], "owner_confirmed", self.settings.dev_reviewer_id,
                                   "Structured owner policy; runtime enforcement is checked separately", "structured_policy", answer.lastrowid)
            c.execute("UPDATE versions SET state='needs_reconciliation',reconciliation_id=NULL,review_revision=review_revision+1 WHERE proposal_id=? AND version=?", (pid, version))
        return self.proposals.view(pid, version)

    def tests(self, artifact):
        report = run(artifact["content"])
        report["artifact_sha256"] = artifact["sha256"]
        with self.store.connect(write=True) as c:
            c.execute("INSERT INTO policy_test_runs VALUES(?,?,?,?,?)", (uid(), artifact["id"], artifact["sha256"], dump(report), now()))
        return report
