"""The AI review never widens access on its own and never overwrites an answer the owner already gave."""
from tools_service.services.autopilot import Autopilot


class Svc:
    def __init__(self, advice_widens, questions=()):
        self.advice_widens, self.accepted, self.saved = advice_widens, False, {}
        self.questions = list(questions)

    def view(self, pid, version):
        return dict(version=1, current_version=1, state="needs_clarification", policy=None, review_revision=1, login=dict(declared=True),
                    requirements=[dict(id="r1", kind="record_scope")], ai_access_advice=dict(widens=self.advice_widens),
                    policy_form=dict(principals=["end_user"], scopes=[dict(requirement_id="r1", mode="checked", reason="own")], checks=[]),
                    content=dict(questions=self.questions), answers={q["id"]: dict(text="mine") for q in self.questions[:1]},
                    reconciliations=[], reconciliation_id=None, can_check_answers=False)

    def suggest_answers(self, pid, version):
        return dict(suggestions=[dict(question_id=q["id"], answer="AI draft", owner_decision=False, options=[]) for q in self.questions])

    def answers(self, pid, version, submission):
        self.saved.update(submission.answers)

    def reconcile(self, *args):
        pass

    def accept_policy(self, pid, version, submission):
        self.accepted = True


def test_advice_that_widens_access_is_left_to_the_owner():
    svc = Svc(advice_widens=True)
    result = Autopilot(svc).run("p", 1)
    assert not svc.accepted and any("wider access" in line for line in result["needs_you"])


def test_least_privilege_advice_is_accepted_and_reported():
    svc = Svc(advice_widens=False)
    result = Autopilot(svc).run("p", 1)
    assert svc.accepted and any("access policy was accepted" in line for line in result["done"])


def test_an_answer_the_owner_gave_is_not_overwritten():
    svc = Svc(advice_widens=False, questions=[dict(id="q1"), dict(id="q2")])
    Autopilot(svc).run("p", 1)
    assert "q1" not in svc.saved and svc.saved.get("q2") == "AI draft"
