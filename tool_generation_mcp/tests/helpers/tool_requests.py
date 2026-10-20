"""Owner-requested tools: the scripted request substitute and its authored triage, suggestion and generation outputs."""
from team_c.models import GenerationOutput, RequestTriageOutput, SuggestionOutput
from helpers.openapi import op
from helpers.publication import RevisingDesk


class RequestDesk(RevisingDesk):
    """AUTHORED TEST SUBSTITUTE: scripted triage, suggestion and (optionally) generation outputs."""
    def __init__(self, settings, store):
        super().__init__(settings, store)
        self.triage, self.ideas, self.generations, self.payloads = [], [], [], []

    def call(self, kind, payload, output_model, run):
        self.payloads.append((kind, payload))
        if kind in ("request_triage", "suggestion") or (kind == "generation" and self.generations):
            self.store.attempt(run, "TEST_SUBSTITUTE", "authored-sandbox-only", "succeeded")
            if kind == "request_triage":
                return RequestTriageOutput.model_validate(self.triage.pop(0))
            if kind == "suggestion":
                return SuggestionOutput.model_validate(dict(suggestions=self.ideas.pop(0) if self.ideas else []))
            return GenerationOutput.model_validate(self.generations.pop(0)(payload["inventory"], self.names))
        return super().call(kind, payload, output_model, run)


def triage(outcome, operation_ids=(), existing=(), questions=(), missing=()):
    return dict(outcome=outcome, summary="sandbox-only triage summary", operation_ids=list(operation_ids), existing_proposal_ids=list(existing),
                questions=list(questions), missing=[dict(kind=k, description=t, operation_ids=list(i)) for k, t, i in missing])


def idea(title, category, operation_ids=(), missing=(), related=(), security=4, others=4):
    """A substitute suggestion, rated strong by default; lower security/others to exercise the potential gate."""
    from team_c.models import POTENTIAL
    return dict(title=title, purpose=f"sandbox-only purpose: {title}", benefit="sandbox-only benefit", business_reason="sandbox-only reason for this service desk",
                category=category, operation_ids=list(operation_ids), related_proposal_ids=list(related), relationship="sandbox-only relationship",
                missing=[dict(kind=k, description=t, operation_ids=list(i)) for k, t, i in missing],
                potential=[dict(criterion=c, score=security if c == "security" else others, reason="sandbox-only rating") for c in POTENTIAL])


def lookup_only(inv, n):
    """AUTHORED: a read-only lookup tool, used when an accepted suggestion names only the lookup operation."""
    lookup = op(inv, "GET", n["prefix"] + n["lookup"])
    return dict(proposals=[dict(name="Booking lookup", description="Find a booking by reference", business_purpose="Let customers check a booking",
                                steps=[dict(id="s1", operation_id=lookup["id"], purpose="Find the booking",
                                            bindings=[dict(target=f'query.{n["ref"]}', kind="runtime_argument", reference="booking_reference", step_id=None, response_status=None)])],
                                configuration=[], outputs=[dict(name="booking", step_id="s1", response_status="200", pointer=f'/{n["lookup_env"]}/{n["record"]}')],
                                questions=[], expected_reads=["Booking"], expected_writes=[], assumptions=[], limitations=[], risk="low", risk_rationale="Read only")],
                capability_gaps=[])


def ask(d, key, goal="sandbox-only: let a customer report a problem with their booking", **extra):
    return d.client.post(f"/api/v1/businesses/{d.bid}/tool-requests", json=dict(goal=goal, examples="sandbox-only: my shower is broken, booking ABC", idempotency_key=key, **extra))


def proposal_count(d):
    return len(d.service.store.all("SELECT id FROM proposals"))
