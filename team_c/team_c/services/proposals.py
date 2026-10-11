from ..compiler.semantics import capability, risks
from ..compiler.readiness import assess
from ..persistence.repositories import policies as policy_repo
import json
import re
from ..config import AppError
from ..grounding import response_only_fields
from ..models import ProposalContent
from .. import requirements
from ..persistence.repositories import enforcement as enforcement_repo, proposals as proposals_repo

API_GAP = "Not possible with this API: "


def api_gap_fields(derived, fields):
    """Carry a version's API gaps and the owner's acknowledgements into new fields, blocking only unacknowledged gaps.

    A gap reported at generation means the tool cannot do part of what was asked; its steps can still only
    call real operations. The owner either accepts the tool without that part (recorded, no model call) or
    asks for a revision. Until one of those happens the gap blocks approval.
    """
    gaps = derived.get("generation_capability_gaps", [])
    acknowledged = derived.get("acknowledged_gaps", [])
    fields["generation_capability_gaps"] = gaps
    if acknowledged:
        fields["acknowledged_gaps"] = acknowledged
    done = {a["index"] for a in acknowledged}
    fields["blockers"].extend(API_GAP + g["explanation"] for i, g in enumerate(gaps) if i not in done)
    return fields


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


ACCESS_QUESTION = re.compile(r"who (may|can|should|is allowed)|authori[sz]|permission|role|owner|owns|belong|access|verif|identity|logged.in|signed.in|authenticat", re.I)
PRINCIPALS = dict(public="anyone without signing in", end_user="signed-in users", resource_owner="signed-in users for records they own",
                  admin="administrators", service="service accounts", guest="guest/session users")


def policy_summary(policy):
    """The owner's accepted access decisions in plain words, to restate where a question asks the same thing."""
    parts = ["Decided in the accepted access policy: this tool may be used by " + ", ".join(PRINCIPALS[p] for p in policy["principals"]) + "."]
    for c in policy.get("checks", []):
        parts.append(f'{c["kind"].capitalize()} check: step {c["step_id"]} reads the {c["resource"]}; its field {c["resource_field"]} in the {c["response_status"]} response '
                     f'must equal the trusted {c["identity_source"]} of the caller, otherwise access is denied.')
    unrestricted = [sc for sc in policy.get("scopes", []) if sc["mode"] == "unrestricted"]
    for sc in policy.get("scopes", []):
        if sc["mode"] == "api_filtered":
            parts.append(f'Data the caller does not select is limited to their own, as filtered by the API; sandbox tests with two identities must prove it ({sc["reason"]}).')
    for sc in unrestricted:
        parts.append(f'Every record is reachable for these callers ({sc["reason"]}).')
    if not policy.get("checks") and not unrestricted:
        # Say what the policy decides about ownership even when it decides "no per-record check",
        # so an ownership question is answered rather than skipped.
        parts.append("No per-record ownership check applies: access is limited to the callers above.")
    if policy.get("roles"):
        parts.append("Required roles (any): " + ", ".join(policy["roles"]) + ".")
    if policy.get("permissions"):
        parts.append("Required permissions (all): " + ", ".join(policy["permissions"]) + ".")
    return " ".join(parts)


def answer_suggestions(view, ops):
    """Editable form defaults for unanswered questions, each with the evidence it comes from.

    An access question is restated from the owner's own accepted policy; a configuration question
    gets the default the API itself declares for the bound input. Nothing is saved or treated as an
    answer until the owner submits it, and submitted answers are checked like any other.
    """
    result = {}
    policy = (view.get("policy") or {}).get("content")
    for q in view["content"]["questions"]:
        if (view["answers"].get(q["id"]) or {}).get("text", "").strip():
            continue
        if q["configuration_key"]:
            for step in view["content"]["steps"]:
                for b in step["bindings"]:
                    schema = (ops.get(step["operation_id"], {}).get("inputs", {}).get(b["target"]) or {}).get("schema") or {}
                    if b["kind"] == "business_configuration" and b["reference"] == q["configuration_key"] and "default" in schema:
                        result[q["id"]] = dict(text=json.dumps(schema["default"]) if not isinstance(schema["default"], str) else schema["default"],
                                               source=f'The API declares {schema["default"]!r} as the default for {b["target"]}. Replace it if your business uses another value.')
        elif policy and ACCESS_QUESTION.search(q["text"]):
            result[q["id"]] = dict(text=policy_summary(policy), source="Restated from the access policy you accepted. Edit it if this question asks something else.")
    return result


def checklist(view):
    """What is left before this version can be approved, in the order an owner resolves it.

    Each item says whether it is done and whether it blocks approval. It restates the rules that
    decide() enforces; it never relaxes them, and decide() remains the authority.
    """
    items = []
    questions = view["content"]["questions"]
    if questions:
        missing = [q["id"] for q in questions if not (view["answers"].get(q["id"]) or {}).get("text", "").strip()]
        items.append(dict(key="questions", label=f"Answer the AI's questions ({len(questions) - len(missing)} of {len(questions)} answered)",
                          done=not missing, href="#questions"))
    if view["requirements"]:
        items.append(dict(key="access", label="Decide who may use this tool and which records it may reach",
                          done=bool(view["policy"]) or view["lifecycle"]["requirements_confirmed"], href="#structured-policy"))
    if view["policy_review_pending"]:
        items.append(dict(key="policy", label="Accept the access policy for this new version", done=False, href="#structured-policy"))
    checked = view["state"] in ("ready_for_review", "approved_to_build") or (view["answers_resolved"] and view["state"] == "needs_clarification")
    current = next((r for r in view["reconciliations"] if r["id"] == view["reconciliation_id"]), None)
    open_findings = [f for f in (current["result"]["findings"] if current else []) if f["status"] != "resolved"]
    items.append(dict(key="check", label="Check the answers" + (f" ({len(open_findings)} need more detail)" if open_findings else ""),
                      done=checked, href="#questions"))
    accepted = {a["index"] for a in view["derived"].get("acknowledged_gaps", [])}
    api_gaps = [i for i in range(len(view["derived"].get("generation_capability_gaps") or [])) if i not in accepted]
    if view["derived"].get("generation_capability_gaps"):
        items.append(dict(key="api_gaps", label="Decide about what the API cannot do (approve without it, or ask for a change)",
                          done=not api_gaps, href=f"#api-gap-{api_gaps[0]}" if api_gaps else "#api-gap-0"))
    if view["open_gaps"]:
        items.append(dict(key="gaps", label="Decide about the reported missing capabilities", done=False, href="#questions"))
    # Before the first check the rating is the generation-time one, which the check re-rates against the
    # owner's answers; it only becomes something to act on once a current check still reports it.
    if view["quality"] and not view["quality"]["ready"] and current:
        items.append(dict(key="quality", label="Resolve the blocking quality findings (ask for a revision)", done=False, href="#revise-proposal"))
    return items


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
        # Evidence-based ownership checks prefill the form only where the owner has decided nothing yet.
        from ..compiler.policies import suggested_checks
        decided = {c["requirement_id"] for c in form_policy.get("checks", []) + form_policy.get("scopes", [])}
        checks = [] if policy else [x for x in suggested_checks(row["content"], inventory, row["requirements"]) if x["requirement_id"] not in decided]
        row["policy_form"]["checks"] = list(form_policy.get("checks", [])) + [x["check"] for x in checks]
        row["policy_form"]["scopes"] = list(form_policy.get("scopes", [])) + [x["scope"] for x in checks]
        row["policy_check_suggestions"] = checks
        from ..compiler.policies import declared_login
        auth, labels = declared_login(row["content"], inventory)
        row["login"] = dict(declared=bool(auth), type=(auth or {}).get("type"), operations=labels)
        advised = self.store.all("SELECT result FROM runs WHERE business_id=? AND kind='access_suggestion' AND status='succeeded' "
                                 "AND json_extract(result,'$.proposal_id')=? AND json_extract(result,'$.version')=? ORDER BY created_at DESC LIMIT 1",
                                 (proposal["business_id"], pid, version))
        row["ai_access_advice"] = json.loads(advised[0]["result"]) if advised and not policy else None
        advice = row["ai_access_advice"]
        if advice:
            scoped = {"record_scope": {"checked", "unrestricted"}, "data_scope": {"api_filtered", "unrestricted"}}
            kinds = {r["id"]: r["kind"] for r in row["requirements"]}
            for a in advice["requirements"]:
                if a["mode"] not in scoped.get(kinds.get(a["requirement_id"]), {"not_applicable"}):
                    a["mode"] = "not_applicable"
            advice["by_requirement"] = {a["requirement_id"]: a for a in advice["requirements"]}
            if not row["login"]["declared"]:
                # Advice stored before this rule, or a model that ignored it, cannot recommend callers the API cannot identify.
                possible = [x for x in advice["principals"] if x in ("public", "guest")]
                if possible != advice["principals"]:
                    advice["principals"], advice["widens"] = possible or ["public"], True
                    advice["principals_reason"] = ("The API declares no login, so only anonymous callers are possible; restricting this tool needs an API that declares authentication. "
                                                   + advice["principals_reason"])
            if not row["policy_form"].get("principals") and not advice["widens"]:
                row["policy_form"]["principals"] = list(advice["principals"])
            decided_scopes = {s["requirement_id"] for s in row["policy_form"].get("scopes", [])}
            row["policy_form"]["scopes"] = list(row["policy_form"].get("scopes", [])) + [
                dict(requirement_id=a["requirement_id"], mode=a["mode"], reason=a["reason"]) for a in advice["requirements"]
                if a["mode"] in ("checked", "api_filtered") and a["requirement_id"] not in decided_scopes]
        row["policy_review_pending"] = bool(row["derived"].get("policy_review_required") and not policy)
        row["capability"] = capability(row["content"], inventory)
        artifact, guards = self.build_evidence(pid, version, row["policy"], row["enforcement"])
        row["readiness"] = assess(row["content"], inventory, policy, artifact, guards)
        row["risk"] = risks(row["content"], inventory, policy)
        from ..quality import assess as assess_quality, existing_tools
        row["quality"] = assess_quality(row["content"], inventory,
                                        existing_tools(self.current_contents(proposal["business_id"])), pid,
                                        current_review=current["result"].get("self_review") if current else None)
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
        row["answer_suggestions"] = answer_suggestions(row, ops)
        # Drafts from the separate answer assistant, for questions that are still unanswered.
        drafted = self.store.all("SELECT result FROM runs WHERE business_id=? AND kind='answer_suggestion' AND status='succeeded' "
                                 "AND json_extract(result,'$.proposal_id')=? AND json_extract(result,'$.version')=? ORDER BY created_at DESC LIMIT 1",
                                 (proposal["business_id"], pid, version))
        open_ids = {q["id"] for q in row["content"]["questions"] if not (row["answers"].get(q["id"]) or {}).get("text", "").strip()}
        row["answers_with_text"] = sorted({q["id"] for q in row["content"]["questions"]} - open_ids)
        accepted = {f["question_id"] for f in (current["result"]["findings"] if current else []) if f["status"] == "resolved"}
        row["ai_answer_suggestions"] = {x["question_id"]: x for x in (json.loads(drafted[0]["result"])["suggestions"] if drafted else []) if x["question_id"] not in accepted}
        row["checklist"] = checklist(row)
        return row

    @staticmethod
    def snapshot(view, checksum):
        snapshot = dict(content=view["content"], answers=view["answers"], checksum=checksum, version=view["version"])
        if view.get("policy"):
            snapshot["policy_sha256"] = view["policy"]["sha256"]
        return snapshot

    def build_evidence(self, pid, version, policy, enforcement):
        """The guard evidence for this version, or (None, None) when it no longer describes it.

        Readiness reports current state, so an artifact built under a policy or enforcement
        configuration that has since changed is not evidence for the proposal as it stands now.
        """
        rows = self.store.all("SELECT * FROM artifacts WHERE proposal_id=? AND version=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (pid, version))
        if not rows:
            return None, None
        row = rows[0]
        content = json.loads(row["content"])
        if (content.get("access_policy") or {}).get("sha256") != (policy or {}).get("sha256"):
            return None, None
        if ((content.get("enforcement_config") or {}).get("id") or None) != ((enforcement or {}).get("id") or None):
            return None, None
        runs = self.store.all("SELECT report FROM policy_test_runs WHERE artifact_id=? AND artifact_sha256=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                              (row["id"], row["sha256"]))
        return content, (json.loads(runs[0]["report"]) if runs else None)

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
