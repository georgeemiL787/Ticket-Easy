"""Test doubles shared by the unit tests."""

from typing import Any

from team_b.contracts.evidence import RiskAssessment
from team_b.domain.understanding import NLUResult


class FakeLLM:
    """An LLMClient that answers from a script. Each response is a dict to return or an exception to raise.

    Calls are recorded in `calls` (system, user, schema_hint, temperature). Running out of responses fails the test.
    """

    def __init__(self, *responses: dict[str, Any] | Exception) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete_json(
        self, *, system: str, user: str, schema_hint: dict[str, Any], temperature: float = 0.0
    ) -> dict[str, Any]:
        self.calls.append({"system": system, "user": user, "schema_hint": schema_hint, "temperature": temperature})
        if not self._responses:
            raise AssertionError("FakeLLM has no scripted response left")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeEvidence:
    """An EvidenceProvider for the safety screen: answers with `assessment`, or raises `error`. Counts calls."""

    def __init__(self, assessment: RiskAssessment | None = None, error: Exception | None = None) -> None:
        self.assessment = assessment or RiskAssessment(flagged=False)
        self.error = error
        self.messages: list[str] = []

    async def classify_risk(
        self, tenant_id: str, message: str, *, request_id: str, conversation_id: str | None = None
    ) -> RiskAssessment:
        self.messages.append(message)
        if self.error is not None:
            raise self.error
        return self.assessment

    async def search_knowledge(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - not used by these tests
        raise NotImplementedError

    async def get_passage(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    async def search_past_tickets(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError


class ScriptedNLU:
    """An NLU that returns a prepared NLUResult (or raises). Records the texts it was given."""

    def __init__(self, result: NLUResult | Exception) -> None:
        self.result = result
        self.texts: list[str] = []

    async def understand(self, text: str, session: Any, tenant: Any) -> NLUResult:
        self.texts.append(text)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result
