"""Let the AI do the review for the owner: access advice, the access policy, the answers to every question and the check.

Each step is what the owner could do by hand on the proposal page, using the same advisors and checks, and everything
it fills in stays editable: the access policy can be reopened, answers are ordinary saved answers, and approving the
tool to be built is still the owner's decision. Two things are never done silently, because they widen what a caller may
reach: public or guest callers and "any record" scopes. Those, and anything the AI cannot settle, are listed for the owner.
"""
from pydantic import ValidationError

from ..compiler.policies import AccessPolicy, PolicySubmission
from ..config import AppError
from ..models import AnswerSubmission

CLOSED = {"approved_to_build", "rejected", "changes_requested", "superseded"}
ROUNDS = 3


class Autopilot:
    def __init__(self, svc):
        self.svc = svc

    def run(self, pid, version):
        """Do every review step the AI can do. Returns {done: [...], needs_you: [...]} in the owner's words."""
        view = self.svc.view(pid, version)
        if view["version"] != view["current_version"] or view["state"] in CLOSED:
            raise AppError("review_closed", "This version is closed; open the current version to review it.", 409)
        done, left = [], []
        self._access(pid, version, done, left)
        self._answers(pid, version, done, left)
        view = self.svc.view(pid, version)
        if view["state"] == "ready_for_review" and not left:
            done.append("The answers passed the check. Review what the AI chose, edit anything you disagree with, then approve the tool to be built.")
        return dict(done=done, needs_you=left)

    # ---- who may use the tool ----

    def _access(self, pid, version, done, left):
        svc = self.svc
        view = svc.view(pid, version)
        if view.get("policy"):
            return
        if view["requirements"] and not view.get("ai_access_advice"):
            try:
                svc.suggest_access(pid, version)
                view = svc.view(pid, version)
                done.append("The AI access advisor chose who may use the tool and which records it may reach (least privilege).")
            except AppError as exc:
                left.append(f"The access advisor could not answer ({exc.message}). Choose who may use the tool.")
                return
        advice, form = view.get("ai_access_advice"), view["policy_form"]
        if advice and advice["widens"]:
            left.append("The advisor suggests wider access (anonymous callers or any record). That is your decision: tick it yourself if you agree.")
            return
        principals = form.get("principals") or (["end_user"] if view["login"]["declared"] and not view["requirements"] else [])
        if not principals:
            left.append("Choose who may use the tool (the API description does not settle it).")
            return
        scoped = [r for r in view["requirements"] if r["kind"] in ("record_scope", "data_scope")]
        decided = {s["requirement_id"] for s in form.get("scopes", [])}
        if [r for r in scoped if r["id"] not in decided]:
            left.append("Choose which records a caller may reach; the AI could not settle it.")
            return
        if any(s["mode"] == "unrestricted" for s in form.get("scopes", [])):
            left.append("The suggested policy lets callers reach any record. That is your decision: choose it yourself if you agree.")
            return
        data = dict(principals=principals, authentication=form.get("authentication", "declared"), role_source=form.get("role_source"),
                    roles=form.get("roles", []), permission_source=form.get("permission_source"), permissions=form.get("permissions", []),
                    scopes=form.get("scopes", []), checks=form.get("checks", []), inputs=form.get("inputs", []),
                    financial=form.get("financial"), irreversible=form.get("irreversible"),
                    external_side_effects=form.get("external_side_effects"), idempotent=form.get("idempotent"),
                    explanation="Filled in by the AI from the API description and the access advisor; the owner can reopen and edit it.")
        try:
            policy = AccessPolicy.model_validate(data)
            svc.accept_policy(pid, version, PolicySubmission(expected_revision=view["review_revision"], policy=policy))
        except ValidationError as exc:
            left.append("The suggested access policy is not complete: " + "; ".join(e["msg"].removeprefix("Value error, ") for e in exc.errors()[:2]))
            return
        except AppError as exc:
            left.append(f"The suggested access policy could not be accepted: {exc.message}")
            return
        done.append("The access policy was accepted from those choices. Reopen it to change anything.")

    # ---- the questions ----

    def _answers(self, pid, version, done, left):
        svc = self.svc
        guessed = []
        for _ in range(ROUNDS):
            view = svc.view(pid, version)
            if view["state"] == "ready_for_review" or not view["content"]["questions"]:
                break
            try:
                drafts = svc.suggest_answers(pid, version)["suggestions"]
            except AppError as exc:
                left.append(f"The answer assistant could not draft answers ({exc.message}). Answer the questions yourself.")
                return
            saved = {k for k, v in view["answers"].items() if (v.get("text") or "").strip()}
            current = next((r for r in view["reconciliations"] if r["id"] == view["reconciliation_id"]), None)
            insufficient = {f["question_id"] for f in (current["result"]["findings"] if current else []) if f["status"] != "resolved"}
            answers = {}
            for draft in drafts:
                qid = draft["question_id"]
                if qid in saved and qid not in insufficient:
                    continue  # an answer the owner (or an earlier round) gave and the check did not object to stays as it is
                text = draft["answer"].strip()
                if not text and draft.get("owner_decision") and draft.get("options"):
                    text = draft["options"][0]  # the assistant's most typical choice, shown to the owner as such
                    guessed.append(qid)
                if text:
                    answers[qid] = text
            if not answers:
                break
            svc.answers(pid, version, AnswerSubmission(expected_revision=view["review_revision"], answers=answers))
            view = svc.view(pid, version)
            if view["can_check_answers"]:
                try:
                    svc.reconcile(pid, version, view["review_revision"])
                except AppError as exc:
                    left.append(f"The answers were saved but could not be checked ({exc.message}).")
                    return
            done.append(f"The AI answered {len(answers)} question{'s' if len(answers) != 1 else ''} from the API description and the business context.")
        view = svc.view(pid, version)
        if guessed:
            left.append(f"{len(guessed)} answer{'s are' if len(guessed) != 1 else ' is'} a business decision the AI chose as the most typical option: check "
                        f"{'them' if len(guessed) != 1 else 'it'} in the questions list and edit if your business differs.")
        if view["state"] != "ready_for_review" and view["content"]["questions"]:
            current = next((r for r in view["reconciliations"] if r["id"] == view["reconciliation_id"]), None)
            open_n = len([f for f in (current["result"]["findings"] if current else []) if f["status"] != "resolved"])
            left.append(f"{open_n or 'Some'} answer{'s' if open_n != 1 else ''} still need{'' if open_n != 1 else 's'} a closer look; the check explains why under each question.")
