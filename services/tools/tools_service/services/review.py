from ..persistence.repositories import policies as policy_repo
import json
from ..config import AppError
from ..grounding import drop_stale_configuration, validate_proposal
from ..models import ProposalContent, GenerationOutput, ReconciliationOutput, RepairOutput, canonical_content
from ..persistence.util import uid, now, dump, digest
from ..diagnostics import check_errors
from .. import requirements
from ..persistence.repositories import proposals as proposals_repo, runs as runs_repo
from .proposals import api_gap_fields, API_GAP, change_summary


class Review:
    """Owner review of one proposal version: answers, reconciliation, decisions, revisions and requirement/gap/question decisions."""

    def __init__(self, settings, store, providers, runs, discovery, proposals):
        self.settings, self.store, self.providers = settings, store, providers
        self.runs, self.discovery, self.proposals = runs, discovery, proposals

    def answers(self, pid, version, submission):
        if any(len(text) > 10000 for text in submission.answers.values()):
            raise AppError("answer_limit", "An answer exceeds 10,000 characters")
        with self.store.connect(write=True) as c:
            row = self.proposals.check_current(c,pid,version,submission.expected_revision)
            accepted = c.execute("SELECT id FROM capability_policies WHERE proposal_id=? AND version=? LIMIT 1", (pid, version)).fetchone()
            access_ids = {r["id"] for r in requirements.ensure(c,pid,version)}
            if accepted and set(submission.answers) & access_ids:
                raise AppError("structured_policy_required", "Edit the structured policy to change access decisions; prose cannot replace it")
            known = {q["id"] for q in json.loads(row["content"])["questions"]} | {r["id"] for r in requirements.ensure(c,pid,version)}
            if not set(submission.answers) <= known:
                raise AppError("unknown_question", "An answer references an unknown question or requirement")
            for qid,text in submission.answers.items():
                c.execute("INSERT INTO answers(proposal_id,version,question_id,text,reviewer,created_at) VALUES(?,?,?,?,?,?)",(pid,version,qid,text.strip(),self.settings.dev_reviewer_id,now()))
            c.execute("UPDATE versions SET state='needs_reconciliation',reconciliation_id=NULL,review_revision=review_revision+1 WHERE proposal_id=? AND version=?",(pid,version))
        return self.proposals.view(pid,version)

    def reconcile(self, pid, version, revision, reuse_only=False):
        """Check the answers of one version. With reuse_only, settle only when no model call is needed and return None otherwise."""
        view = self.proposals.view(pid,version)
        with self.store.connect() as c:
            self.proposals.check_current(c,pid,version,revision)
        if not view["can_check_answers"]:
            raise AppError("answers_required", "Save answers to the questions and access requirements before checking them. No saved nonempty answers were found.")
        spec = self.discovery.spec(view["spec_id"])
        snapshot = self.proposals.snapshot(view,spec["checksum"])
        scope = view["derived"].get("generation_scope")
        reqs = view["requirements"]
        latest = {a["id"] for a in view["answers"].values()}
        payload = dict(proposal=snapshot,earlier_answers=[a for a in view["answer_history"] if a["id"] not in latest],requirements=[{k:r[k] for k in ("id","text","source_facts")} for r in reqs],inventory=self.proposals.scoped(spec["inventory"],scope),business=self.store.one("SELECT * FROM businesses WHERE id=?",(view["business_id"],)))
        structured = {r["id"] for r in reqs if view.get("policy") and r["events"] and r["events"][-1]["basis"] == "structured_policy" and r["status"] == "owner_confirmed"}
        # A requirement with no answer cannot be resolved by any assessment (the prompt itself says a
        # blank answer is insufficient), so it is recorded as unanswered without asking the model.
        unanswered = {r["id"] for r in reqs if r["id"] not in structured and not (view["answers"].get(r["id"]) or {}).get("text", "").strip()}
        payload["requirements"] = [r for r in payload["requirements"] if r["id"] not in structured | unanswered]
        if unanswered:
            payload["pending_access_decisions"] = [r["text"] for r in reqs if r["id"] in unanswered]
        old = canonical_content(ProposalContent.model_validate(view["content"]))
        question_ids = {q.id for q in old.questions}
        expected = question_ids | {r["id"] for r in reqs}
        assessed = question_ids | {r["id"] for r in payload["requirements"]}
        # The model sees only the answers it assesses, as id/question/text. Structured decisions are
        # stored as answers holding the whole accepted policy, which no assessment reads.
        compact = lambda a: dict(id=a["id"], question_id=a["question_id"], text=a["text"])
        payload["proposal"] = dict(snapshot, answers={k: compact(a) for k, a in snapshot["answers"].items() if k in assessed})
        payload["earlier_answers"] = [compact(a) for a in payload["earlier_answers"] if a["question_id"] in assessed]
        earlier = []
        reused = self.reusable_assessment(view, assessed, old)
        if reused:
            # Nothing the model would assess changed since an earlier check of this same version, so its
            # assessment is reused instead of asking again; the deterministic checks below still run.
            result, source = reused
            run = self.store.start_run(view["business_id"], "reconciliation", payload)
            self.store.diagnostic(run, "reused_assessment", dict(source_reconciliation_id=source, model_calls=0,
                                                                 items=sorted(f.question_id for f in result.findings)))
        elif reuse_only:
            return None
        else:
            run,result = self.runs.model_run(view["business_id"],"reconciliation",payload,ReconciliationOutput)
        rejected = None if reused else self.rejected_revision(result,spec,scope)
        if rejected:
            # One automatic retry: the model sees its rejected revised proposal and exactly what the check found.
            self.runs.fail_run(run,rejected)
            earlier = [run]
            payload = dict(payload,grounding_feedback=dict(previous_proposal=result.revised_proposal.model_dump(),errors=check_errors(rejected.details["errors"],self.settings.model_secrets)))
            run,result = self.runs.model_run(view["business_id"],"reconciliation",payload,ReconciliationOutput)
        mis_cited = None if reused else self.mis_cited_evidence(result,view,expected,structured|unanswered)
        if mis_cited:
            # One automatic retry: the model sees which items cited an answer that is not their own.
            # The evidence check runs again on the retry, so a repeat still fails.
            self.runs.fail_run(run,AppError("invalid_reconciliation","Finding cites nonexistent or unrelated answer evidence",details={"findings":mis_cited}))
            earlier.append(run)
            payload = dict(payload,evidence_feedback=[dict(question_id=qid,
                                                           answer_revision_id=view["answers"][qid]["id"] if qid in view["answers"] else None)
                                                      for qid in sorted(set(mis_cited))])
            run,result = self.runs.model_run(view["business_id"],"reconciliation",payload,ReconciliationOutput)
        try:
            from ..models import Finding
            result.findings = ([f for f in result.findings if f.question_id not in structured | unanswered]
                               + [Finding(question_id=qid, status="resolved", explanation="Explicit structured owner decision; enforcement is separately compiled and tested", answer_revision_ids=[view["answers"][qid]["id"]]) for qid in sorted(structured)]
                               + [Finding(question_id=qid, status="insufficient", explanation="Not decided yet. Choose who may use this tool and which records it may reach in the access policy.", answer_revision_ids=[]) for qid in sorted(unanswered)])
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
            candidate = self.runs.tidy(run, canonical_content(result.revised_proposal or old))
            # A revised proposal that omits the rating does not thereby drop it: the rating is not
            # content the answers changed, so keep the one this version already carries.
            if candidate.self_review is None:
                candidate.self_review = old.self_review
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
            # An original capability gap cannot disappear through an answer-only reconciliation: the owner
            # accepts the tool without that part (accept_api_gap) or asks for a revision.
            api_gap_fields(view["derived"], fields)
            changed = digest(candidate.model_dump()) != digest(old.model_dump())
            all_resolved = all(f.status == "resolved" for f in findings.values()) and not result.capability_gaps and not fields["blockers"]
            with self.store.connect(write=True) as c:
                self.proposals.check_current(c,pid,version,revision)
                rec_id = uid()
                result_data = result.model_dump()
                result_data["findings"] = [f.model_dump() for f in findings.values()]
                result_data["validation_blockers"] = fields["blockers"]
                if reused:
                    # The earlier check reused, or "generation" when nothing was left for a model to judge.
                    result_data["reused_from"] = reused[1] or "generation"
                c.execute("INSERT INTO reconciliations VALUES(?,?,?,?,?,?,?,?)",(rec_id,pid,version,digest(snapshot),dump(result_data),int(all_resolved and not changed),run,now()))
                requirements.ensure(c,pid,version)
                for r in reqs:
                    f, answer = findings[r["id"]], view["answers"].get(r["id"])
                    # A sufficient assessment of an already confirmed answer keeps the confirmation.
                    if not (f.status == "resolved" and r["status"] == "owner_confirmed"):
                        requirements.event(c,pid,version,r["id"],"answer_sufficient" if f.status == "resolved" else f.status,"model",f.explanation,"model_assessment",answer["id"] if answer else None,rec_id)
                        # TOOLS_DEV_FAST_TRACK: auto-confirm sufficient requirements without a manual click.
                        if self.settings.dev_fast_track and f.status == "resolved" and answer:
                            requirements.event(c,pid,version,r["id"],"owner_confirmed",self.settings.dev_reviewer_id,"Auto-confirmed (TOOLS_DEV_FAST_TRACK); runtime enforcement is not implemented.","owner_confirmed",answer["id"],rec_id)
                if changed:
                    new_version = proposals_repo.replace_version(c,pid,version,view["spec_id"],candidate.model_dump(),fields,run,"needs_reconciliation")
                    # Copy answers as new evidence, explicitly requiring another reconciliation.
                    proposals_repo.copy_answers(c,pid,new_version,view["answers"].values())
                    requirements.ensure(c,pid,new_version)
                    response = dict(run_id=run,version=new_version,state="needs_reconciliation",material_change=True)
                else:
                    state = "ready_for_review" if all_resolved else "needs_clarification"
                    c.execute("UPDATE versions SET state=?,derived=?,reconciliation_id=?,review_revision=review_revision+1 WHERE proposal_id=? AND version=?",(state,dump(fields),rec_id,pid,version))
                    response = dict(run_id=run,version=version,state=state,material_change=False)
                runs_repo.succeed(c,run,response)
            return response
        except Exception as exc:
            self.runs.fail_run(run,exc)
            if earlier and isinstance(exc, AppError):
                exc.details["earlier_run_ids"] = earlier
            raise

    @staticmethod
    def reusable_assessment(view, ids, content):
        """An earlier assessment of this version that still answers every item in ids, or None.

        Version content is immutable and answers are append-only, so an earlier reconciliation that
        resolved each item while citing the answer that is still its latest saw exactly the evidence
        the model would see now. Only an assessment that changed nothing, reported no missing
        capability and rated the proposal usable (as the approval gate judges it) is reused: anything else is left for the model to
        reassess, so a stale verdict can neither block nor pass approval. Returns (output, source id).
        """
        from ..quality import PASS_SCORE
        # The same rule the approval gate applies, so a reused rating never settles a review it would then block.
        usable = lambda review: bool(review) and review.get("verdict") == "proceed" and all(c["score"] >= PASS_SCORE for c in review.get("criteria", []))
        latest = {i: (view["answers"].get(i) or {}).get("id") for i in ids}
        if any(v is None for v in latest.values()):
            return None
        for rec in reversed(view["reconciliations"]):
            res = rec["result"]
            review = res.get("self_review")
            if res.get("revised_proposal") or res.get("capability_gaps") or not usable(review):
                continue
            found = {f["question_id"]: f for f in res.get("findings", [])}
            if all(found.get(i, {}).get("status") == "resolved" and latest[i] in found[i]["answer_revision_ids"] for i in ids):
                return ReconciliationOutput(findings=[found[i] for i in sorted(ids)], revised_proposal=None, capability_gaps=[], self_review=review), rec["id"]
        if not ids and content.self_review and usable(content.self_review.model_dump()):
            # Nothing is left for a model to judge (no questions; every access decision is structured).
            return ReconciliationOutput(findings=[], revised_proposal=None, capability_gaps=[], self_review=content.self_review), None
        return None

    def suggest_answers(self, pid, version):
        """Draft answers to this version's open questions with a separate answer assistant.

        The assistant is isolated from reconciliation: its own prompt and run, it never sees findings,
        and the reviewer later judges whatever the owner submits on the text alone. Retrieval is done
        here rather than by a tool loop: the proposal's operations, plus the operations of the whole
        API ranked by the questions' wording. Drafts are stored on the run, never as answers. An
        identical request (same questions, proposal, policy and evidence) reuses the earlier drafts.
        """
        view = self.proposals.view(pid, version)
        # Every question not yet accepted by the current check: unanswered ones, and answered ones the check
        # found insufficient or contradictory. The assistant sees questions only, never answers or findings.
        current = next((r for r in view["reconciliations"] if r["id"] == view["reconciliation_id"]), None)
        accepted = {f["question_id"] for f in (current["result"]["findings"] if current else []) if f["status"] == "resolved"}
        questions = [dict(id=q["id"], text=q["text"], configuration_key=q["configuration_key"]) for q in view["content"]["questions"] if q["id"] not in accepted]
        if not questions:
            raise AppError("nothing_to_suggest", "Every question already has an accepted answer")
        spec = self.discovery.spec(view["spec_id"])
        scope = view["derived"].get("generation_scope")
        used = {s["operation_id"] for s in view["content"]["steps"]}
        from ..capabilities import operation_index
        related, _ = operation_index(spec["inventory"], 2500, exclude_ids=used, goal=" ".join(q["text"] for q in questions))
        business = self.store.one("SELECT name,description FROM businesses WHERE id=?", (view["business_id"],))
        content = {k: view["content"][k] for k in ("name", "description", "business_purpose", "steps", "configuration", "expected_reads", "expected_writes", "assumptions", "limitations")}
        payload = dict(business=business, proposal=content, questions=questions, access_policy=(view.get("policy") or {}).get("content"),
                       inventory=self.proposals.scoped(spec["inventory"], scope), related_operations=[o for o in related if o["status"] == "eligible"])
        cached = self.store.all("SELECT result FROM runs WHERE business_id=? AND kind='answer_suggestion' AND status='succeeded' AND input_hash=? ORDER BY created_at DESC LIMIT 1",
                                (view["business_id"], digest(payload)))
        if cached:
            return json.loads(cached[0]["result"])
        from ..models import AnswerSuggestionOutput
        run, output = self.runs.model_run(view["business_id"], "answer_suggestion", payload, AnswerSuggestionOutput)
        if sorted(x.question_id for x in output.suggestions) != sorted(q["id"] for q in questions):
            exc = AppError("invalid_model_output", "The answer assistant must draft exactly one answer per open question", 502)
            self.runs.fail_run(run, exc)
            raise exc
        for x in output.suggestions:
            # An owner decision carries options only: a draft answer there would be a guessed business rule.
            if x.owner_decision:
                x.answer = ""
        result = dict(proposal_id=pid, version=version, suggestions=[x.model_dump() for x in output.suggestions])
        self.store.finish_run(run, result)
        return result

    def suggest_access(self, pid, version):
        """Least-privilege access advice from a separate advisor; stored on its run, never accepted on the owner's behalf.

        The advisor sees the purpose, the operations and the open requirements, never answers or check findings.
        Advice that would widen access (public or guest callers, or any-record scopes) is marked so the page shows
        it without preselecting it. An identical request reuses the earlier advice.
        """
        view = self.proposals.view(pid, version)
        if not view["requirements"]:
            raise AppError("nothing_to_suggest", "This tool has no access requirements to advise on")
        spec = self.discovery.spec(view["spec_id"])
        business = self.store.one("SELECT name,description FROM businesses WHERE id=?", (view["business_id"],))
        content = {k: view["content"][k] for k in ("name", "description", "business_purpose", "steps", "expected_reads", "expected_writes", "limitations")}
        payload = dict(business=business, proposal=content, requirements=[{k: r[k] for k in ("id", "kind", "text", "operations")} for r in view["requirements"]],
                       login=dict(declared=view["login"]["declared"], note="Without a declared login only public or guest callers are possible." if not view["login"]["declared"] else "Callers log in through the declared API authentication."),
                       ownership_evidence=[x["evidence"] for x in view.get("policy_check_suggestions", [])],
                       inventory=self.proposals.scoped(spec["inventory"], view["derived"].get("generation_scope")))
        cached = self.store.all("SELECT result FROM runs WHERE business_id=? AND kind='access_suggestion' AND status='succeeded' AND input_hash=? ORDER BY created_at DESC LIMIT 1",
                                (view["business_id"], digest(payload)))
        if cached:
            return json.loads(cached[0]["result"])
        from ..models import AccessAdviceOutput
        run, output = self.runs.model_run(view["business_id"], "access_suggestion", payload, AccessAdviceOutput)
        kinds = {r["id"]: r["kind"] for r in view["requirements"]}
        valid = {"record_scope": {"checked", "unrestricted"}, "data_scope": {"api_filtered", "unrestricted"}}
        if sorted(a.requirement_id for a in output.requirements) != sorted(kinds):
            exc = AppError("invalid_model_output", "The access advisor must advise on each requirement exactly once", 502)
            self.runs.fail_run(run, exc)
            raise exc
        advice = []
        for a in output.requirements:
            item = a.model_dump()
            if a.mode not in valid.get(kinds[a.requirement_id], {"not_applicable"}):
                # A mode this requirement cannot take is no advice: scopes exist only for record and data scope.
                item.update(mode="not_applicable", owner_decision=kinds[a.requirement_id] in valid)
            item["widens"] = item["mode"] == "unrestricted"
            advice.append(item)
        principals, reason = list(output.principals), output.principals_reason
        if not view["login"]["declared"] and set(principals) - {"public", "guest"}:
            principals = [x for x in principals if x in ("public", "guest")] or ["public"]
            reason = "The API declares no login, so only anonymous callers are possible; restricting this tool needs an API that declares authentication. " + reason
        result = dict(proposal_id=pid, version=version, principals=principals, principals_reason=reason,
                      principals_confidence=output.principals_confidence, widens=bool(set(principals) & {"public", "guest"}), requirements=advice)
        self.store.finish_run(run, result)
        return result

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

    @staticmethod
    def mis_cited_evidence(result, view, expected, structured):
        """Findings citing an answer revision that belongs to another question or requirement.

        Only assessed items are reported, so an assessment that is wrong in a different way still
        surfaces its own error instead of this one. Structured owner decisions are excluded because
        their findings are rewritten from the accepted policy before the evidence check runs.
        """
        history = {a["id"]: a for a in view["answer_history"]}
        return [f.question_id for f in result.findings
                if f.question_id in expected and f.question_id not in structured
                and any(i not in history or history[i]["question_id"] != f.question_id for i in f.answer_revision_ids)]

    def decide(self,pid,version,submission):
        request_hash = digest(dict(pid=pid,version=version,action=submission.action,reason=submission.reason,reviewer=self.settings.dev_reviewer_id))
        with self.store.connect(write=True) as c:
            prior = c.execute("SELECT * FROM decisions WHERE idempotency_key=? OR (proposal_id=? AND version=?)",(submission.idempotency_key,pid,version)).fetchone()
            if prior:
                if prior["request_hash"] == request_hash:
                    return dict(prior)
                raise AppError("decision_conflict","A different decision already exists or this idempotency key was used",409)
            row = self.proposals.check_current(c,pid,version,submission.expected_revision)
            answers = proposals_repo.latest_answers(c,pid,version)
            spec = dict(c.execute("SELECT checksum,inventory FROM specifications WHERE id=?",(row["spec_id"],)).fetchone())
            snapshot = dict(content=json.loads(row["content"]),answers=answers,checksum=spec["checksum"],version=version)
            policy_row = c.execute("SELECT sha256 FROM capability_policies WHERE proposal_id=? AND version=? ORDER BY sequence DESC LIMIT 1", (pid, version)).fetchone()
            if policy_row:
                snapshot["policy_sha256"] = policy_row["sha256"]
            if submission.action == "approve_to_build":
                if json.loads(row["derived"]).get("policy_review_required") and not policy_row:
                    raise AppError("policy_review_required", "Accept the structured policy before approving this policy-review version")
                rec = c.execute("SELECT * FROM reconciliations WHERE id=?",(row["reconciliation_id"],)).fetchone()
                if row["state"] != "ready_for_review" or not rec or not rec["successful"] or rec["snapshot_hash"] != digest(snapshot):
                    raise AppError("approval_blocked","Approval requires current successful reconciliation and validation")
                fields = validate_proposal(ProposalContent.model_validate(snapshot["content"]),json.loads(spec["inventory"]),json.loads(row["derived"]).get("generation_scope"))
                if fields["blockers"]:
                    raise AppError("approval_blocked","Unresolved configuration remains")
                # A proposal that duplicates an existing tool, or that the model itself rated
                # below usable, must be revised rather than approved. The rating used is the one
                # produced by the reconciliation above, not the generation-time rating those
                # answers have since overtaken.
                from ..quality import assess as assess_quality, existing_tools
                business_id = c.execute("SELECT business_id FROM proposals WHERE id=?", (pid,)).fetchone()["business_id"]
                quality = assess_quality(snapshot["content"], json.loads(spec["inventory"]),
                                         existing_tools(self.proposals.current_contents(business_id)), pid,
                                         current_review=json.loads(rec["result"]).get("self_review"))
                if quality["blockers"]:
                    raise AppError("approval_blocked", "This proposal is not worth approving yet: "
                                  + "; ".join(f["summary"] for f in quality["blockers"]),
                                  details={"quality": quality})
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
        view=self.proposals.view(pid)
        with self.store.connect() as c:
            self.proposals.check_current(c,pid,view["version"],submission.expected_revision,True)
        spec=self.discovery.spec(view["spec_id"])
        scope=view["derived"].get("generation_scope")
        payload=dict(proposal=view["content"],instruction=submission.instruction,answer_history=view["answer_history"],inventory=self.proposals.scoped(spec["inventory"],scope),business=self.store.one("SELECT * FROM businesses WHERE id=?",(view["business_id"],)))
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
            content=self.runs.tidy(run,canonical_content(proposal))
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
                self.proposals.check_current(c,pid,view["version"],submission.expected_revision,True)
                version=proposals_repo.replace_version(c,pid,view["version"],view["spec_id"],content.model_dump(),fields,run,"needs_clarification")
                state=self.proposals.settle(c,pid,version,content,fields)
                response=dict(run_id=run,version=version,state=state)
                runs_repo.succeed(c,run,response)
            return response
        except Exception as exc:
            self.runs.fail_run(run,exc)
            raise

    def confirm_requirement(self, pid, version, requirement_id, submission):
        """Owner confirmation of an answer the current reconciliation assessed as sufficient."""
        with self.store.connect(write=True) as c:
            row = self.proposals.check_current(c,pid,version,submission.expected_revision)
            reqs = requirements.ensure(c,pid,version)
            if requirement_id not in {r["id"] for r in reqs}:
                raise AppError("unknown_requirement","This version has no such access requirement",404)
            answers = proposals_repo.latest_answers(c,pid,version)
            current = next(r for r in requirements.current(c,pid,version,reqs,answers,row["reconciliation_id"]) if r["id"] == requirement_id)
            if current["status"] != "answer_sufficient":
                raise AppError("confirmation_blocked",f"Only an answer the current reconciliation assessed as sufficient can be confirmed (status: {current['status']})")
            requirements.event(c,pid,version,requirement_id,"owner_confirmed",self.settings.dev_reviewer_id,"Owner confirmed this answer as intended behavior; runtime enforcement is not implemented.","owner_confirmed",current["answer"]["id"],row["reconciliation_id"])
            c.execute("UPDATE versions SET review_revision=review_revision+1 WHERE proposal_id=? AND version=?",(pid,version))
        return self.proposals.view(pid,version)

    def dismiss_gap(self, pid, version, gap_index, submission):
        """Owner decision that a missing capability reported by the current answer check is not needed for this tool.
        Once every reported gap is dismissed and every answer was resolved without validation blockers, the version is ready for review."""
        with self.store.connect(write=True) as c:
            row = self.proposals.check_current(c,pid,version,submission.expected_revision)
            rec = c.execute("SELECT * FROM reconciliations WHERE id=?",(row["reconciliation_id"],)).fetchone() if row["reconciliation_id"] else None
            if not rec:
                raise AppError("no_reconciliation","Check the answers before deciding about a reported missing capability",409)
            result = json.loads(rec["result"])
            gaps = result.get("capability_gaps") or []
            if not 0 <= gap_index < len(gaps):
                raise AppError("unknown_gap","The current answer check reported no such missing capability",404)
            c.execute("INSERT OR IGNORE INTO gap_dismissals VALUES(?,?,?,?,?,?,?)",(uid(),rec["id"],gap_index,gaps[gap_index]["requested_capability"],submission.reason.strip(),self.settings.dev_reviewer_id,now()))
            dismissed = {r[0] for r in c.execute("SELECT gap_index FROM gap_dismissals WHERE reconciliation_id=?",(rec["id"],))}
            ready = all(f["status"] == "resolved" for f in result["findings"]) and not result.get("validation_blockers") and dismissed >= set(range(len(gaps)))
            if ready:
                c.execute("UPDATE reconciliations SET successful=1 WHERE id=?",(rec["id"],))
            c.execute("UPDATE versions SET state=?,review_revision=review_revision+1 WHERE proposal_id=? AND version=?",("ready_for_review" if ready else row["state"],pid,version))
        return self.proposals.view(pid,version)

    def accept_api_gap(self, pid, version, gap_index, submission):
        """Owner decision to build the tool without a part the API cannot do. Recorded on the version; no model call.

        If the current answer check failed only because of API gaps that are now all accepted, it now counts,
        exactly as when a reported missing capability is marked not needed.
        """
        with self.store.connect(write=True) as c:
            row = self.proposals.check_current(c, pid, version, submission.expected_revision)
            derived = json.loads(row["derived"])
            gaps = derived.get("generation_capability_gaps") or []
            if not 0 <= gap_index < len(gaps):
                raise AppError("unknown_gap", "This version reports no such missing API capability", 404)
            acknowledged = [a for a in derived.get("acknowledged_gaps", []) if a["index"] != gap_index]
            acknowledged.append(dict(index=gap_index, requested_capability=gaps[gap_index]["requested_capability"], reason=submission.reason.strip(),
                                     reviewer=self.settings.dev_reviewer_id, at=now()))
            derived["acknowledged_gaps"] = acknowledged
            # Older versions recorded the same gap under earlier wordings.
            stale = {prefix + gaps[gap_index]["explanation"] for prefix in (API_GAP, "Requires explicit revision: ", "Generation capability gap: ")}
            derived["blockers"] = [b for b in derived.get("blockers", []) if b not in stale]
            state = row["state"]
            rec = c.execute("SELECT * FROM reconciliations WHERE id=?", (row["reconciliation_id"],)).fetchone() if row["reconciliation_id"] else None
            if rec:
                result = json.loads(rec["result"])
                result["validation_blockers"] = [b for b in result.get("validation_blockers", []) if b not in stale]
                result.setdefault("accepted_api_gaps", []).append(gap_index)
                dismissed = {r[0] for r in c.execute("SELECT gap_index FROM gap_dismissals WHERE reconciliation_id=?", (rec["id"],))}
                ready = (all(f["status"] == "resolved" for f in result["findings"]) and not result["validation_blockers"]
                         and dismissed >= set(range(len(result.get("capability_gaps") or []))))
                c.execute("UPDATE reconciliations SET result=?,successful=? WHERE id=?", (dump(result), int(ready or rec["successful"]), rec["id"]))
                if ready:
                    state = "ready_for_review"
            c.execute("UPDATE versions SET derived=?,state=?,review_revision=review_revision+1 WHERE proposal_id=? AND version=?", (dump(derived), state, pid, version))
        return self.proposals.view(pid, version)

    def supersede_question(self, pid, version, question_id, submission):
        """Replace a version with one without an invalid question; the question, reason and answers stay in history."""
        view = self.proposals.view(pid,version)
        old = ProposalContent.model_validate(view["content"])
        question = next((q for q in old.questions if q.id == question_id),None)
        if not question:
            raise AppError("unknown_question","This version has no such question",404)
        if question.configuration_key:
            raise AppError("question_configuration","This question establishes a configuration value; request a revision instead")
        content = canonical_content(old.model_copy(update=dict(questions=[q for q in old.questions if q.id != question_id])))
        spec = self.discovery.spec(view["spec_id"])
        scope = view["derived"].get("generation_scope")
        fields = validate_proposal(content,spec["inventory"],scope)
        if scope is not None:
            fields["generation_scope"] = scope
        api_gap_fields(view["derived"], fields)
        run = self.store.start_run(view["business_id"],"question_supersession",dict(proposal_id=pid,version=version,question_id=question_id,reason=submission.reason))
        try:
            with self.store.connect(write=True) as c:
                self.proposals.check_current(c,pid,version,submission.expected_revision)
                new_version = proposals_repo.replace_version(c,pid,version,view["spec_id"],content.model_dump(),fields,run,"needs_clarification")
                c.execute("INSERT INTO question_supersessions VALUES(?,?,?,?,?,?,?,?,?)",(uid(),pid,version,question_id,question.text,submission.reason.strip(),self.settings.dev_reviewer_id,new_version,now()))
                proposals_repo.copy_answers(c,pid,new_version,[a for a in view["answers"].values() if a["question_id"] != question_id])
                state = self.proposals.settle(c,pid,new_version,content,fields)
                response = dict(run_id=run,version=new_version,state=state)
                runs_repo.succeed(c,run,response)
            return response
        except Exception as exc:
            self.runs.fail_run(run,exc)
            raise
