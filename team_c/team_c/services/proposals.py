from ..compiler.semantics import capability, risks
from ..compiler.readiness import assess
from ..persistence.repositories import policies as policy_repo
import json
from ..config import AppError
from ..grounding import response_only_fields
from ..models import ProposalContent
from .. import requirements
from ..persistence.repositories import enforcement as enforcement_repo, proposals as proposals_repo

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


class Proposals:
    """The authoritative proposal rules: the review view, approval snapshots, revision checks and settled state."""

    def __init__(self, settings, store, providers, discovery):
        self.settings, self.store, self.providers = settings, store, providers
        self.discovery = discovery

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
            rec["dismissals"] = {d["gap_index"]: d for d in self.store.all("SELECT * FROM gap_dismissals WHERE reconciliation_id=?", (rec["id"],))}
        current = next((r for r in row["reconciliations"] if r["id"] == row["reconciliation_id"]), None)
        row["open_gaps"] = [i for i in range(len(current["result"].get("capability_gaps") or [])) if i not in current["dismissals"]] if current else []
        row["answers_resolved"] = bool(current) and all(f["status"] == "resolved" for f in current["result"]["findings"]) and not current["result"].get("validation_blockers")
        row["attempts"] = self.store.all("SELECT * FROM attempts WHERE run_id=? ORDER BY id", (row["run_id"],))
        inventory = self.discovery.spec(row["spec_id"])["inventory"]
        ops = {o["id"]: o for o in inventory["operations"]}
        with self.store.connect() as c:
            reqs = requirements.derive(row["content"]["steps"], inventory)
            row["requirements"] = requirements.current(c, pid, version, reqs, row["answers"], row["reconciliation_id"])
        assessed = {q["id"] for q in row["content"]["questions"]} | {r["id"] for r in row["requirements"]}
        row["can_check_answers"] = not assessed or any(row["answers"].get(qid, {}).get("text", "").strip() for qid in assessed)
        row["supersessions"] = self.store.all("SELECT * FROM question_supersessions WHERE proposal_id=? ORDER BY created_at", (pid,))
        row["repairs"] = self.store.all("SELECT * FROM repairs WHERE proposal_id=? ORDER BY created_at", (pid,))
        row["enforcement"] = enforcement_repo.current(self.store, pid, version)
        row["change_summary"] = []
        if version > 1:
            previous = proposals_repo.version_content(self.store, pid, version - 1)
            labels = {i: f'{o["method"]} {o["path"]}' for i, o in ops.items()}
            row["change_summary"] = change_summary(previous, row["content"], labels, [s for s in row["supersessions"] if s["new_version"] == version])
        row["question_review"] = response_only_fields(ProposalContent.model_validate(row["content"]), ops)
        row["operation_states"] = {s["operation_id"]: dict(technically_supported=ops[s["operation_id"]].get("supported", False), proposal_eligible=ops[s["operation_id"]].get("proposal_eligible", ops[s["operation_id"]].get("supported", False))) for s in row["content"]["steps"] if s["operation_id"] in ops}
        row["lifecycle"] = dict(approved_to_build=row["state"] == "approved_to_build", requirements_confirmed=all(r["status"] == "owner_confirmed" for r in row["requirements"]), runtime_ready=False, note=requirements.RUNTIME_NOTE)
        row["policy"] = policy_repo.current(self.store, pid, version)
        policy = row["policy"]["content"] if row["policy"] else None
        from ..compiler.policies import suggested_inputs
        form_policy = dict(policy or row["derived"].get("policy_draft", {}))
        suggestions = suggested_inputs(row["content"], inventory, form_policy.get("inputs", []))
        row["policy_form"] = dict(form_policy, inputs=list(form_policy.get("inputs", [])) + suggestions)
        row["policy_suggestions"] = suggestions
        row["policy_review_pending"] = bool(row["derived"].get("policy_review_required") and not policy)
        row["capability"] = capability(row["content"], inventory)
        row["readiness"] = assess(row["content"], inventory, policy)
        row["risk"] = risks(row["content"], inventory, policy)
        from ..compiler.policies import resolve_inputs, runtime_exposure_allowed
        from ..compiler.semantics import input_semantics
        effective, protected, unresolved = resolve_inputs(row["content"], inventory, policy)
        choices = {(i["step_id"], i["target"]): i for i in (policy or {}).get("inputs", [])}
        arguments, blocked_arguments = set(), set()
        for step in effective["steps"]:
            semantics = input_semantics(ops[step["operation_id"]])
            for binding in step["bindings"]:
                if binding["kind"] == "runtime_argument":
                    arguments.add(binding["reference"])
                    if not runtime_exposure_allowed(semantics, binding["target"], choices.get((step["id"], binding["target"]))):
                        blocked_arguments.add(binding["reference"])
        row["interface_preview"] = dict(arguments=sorted(arguments - blocked_arguments), blocked_arguments=sorted(blocked_arguments),
                                        protected=protected, blockers=unresolved, executable=False)
        if policy:
            resolved = {(i["step_id"], i["target"]) for i in policy["inputs"]}
            row["capability"]["questions"] = [q for q in row["capability"]["questions"] if (q["step_id"], q["input"]) not in resolved]
        return row

    @staticmethod
    def snapshot(view, checksum):
        snapshot = dict(content=view["content"], answers=view["answers"], checksum=checksum, version=view["version"])
        if view.get("policy"):
            snapshot["policy_sha256"] = view["policy"]["sha256"]
        return snapshot

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

    @staticmethod
    def scoped(inventory, scope):
        return inventory if scope is None else dict(inventory, operations=[o for o in inventory["operations"] if o["id"] in scope])

    def current_contents(self, business_id):
        return proposals_repo.current_contents(self.store, business_id)

    def existing_tools(self, business_id):
        built = {r["proposal_id"] for r in self.store.all("SELECT DISTINCT a.proposal_id FROM artifacts a JOIN proposals p ON p.id=a.proposal_id WHERE p.business_id=?", (business_id,))}
        live = {r["proposal_id"] for r in self.store.all("SELECT DISTINCT proposal_id FROM publications WHERE business_id=? AND status='published'", (business_id,))}
        return [dict(proposal_id=r["proposal_id"], name=r["content"]["name"], purpose=r["content"]["business_purpose"], state=r["state"], version=r["version"],
                     operation_ids=[s["operation_id"] for s in r["content"]["steps"]], built=r["proposal_id"] in built, published=r["proposal_id"] in live)
                for r in self.current_contents(business_id)]
