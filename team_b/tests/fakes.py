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


class ScriptedMovePicker:
    """A MovePicker that returns prepared moves (or plans, or raises), one per ask. Records every view it was given.

    With repeat_last=True the last prepared answer is given again for as long as it is asked (an endless loop)."""

    def __init__(self, *answers: Any, repeat_last: bool = False) -> None:
        self._answers = list(answers)
        self._repeat_last = repeat_last
        self.views: list[Any] = []

    async def next_move(self, view: Any) -> Any:
        self.views.append(view)
        if not self._answers:
            raise AssertionError("ScriptedMovePicker has no prepared answer left")
        answer = self._answers[0] if (self._repeat_last and len(self._answers) == 1) else self._answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class FunctionPicker:
    """A MovePicker that decides with a function of the view (a tiny scripted orchestrator). Records the views."""

    def __init__(self, decide: Any) -> None:
        self._decide = decide
        self.views: list[Any] = []

    async def next_move(self, view: Any) -> Any:
        self.views.append(view)
        return self._decide(view)


class KnowledgeFakeLLM:
    """A deterministic stand-in for the knowledge agent's model: it reads the task and answers like a careful one.

    query: the customer's message; requery: the same words again unless told otherwise (a real model would also give
    up on a question the documents do not cover); answer: the first passage, word for word, cited."""

    def __init__(self, requery: str = "") -> None:
        self.requery = requery
        self.tasks: list[str] = []

    async def complete_json(
        self, *, system: str, user: str, schema_hint: dict[str, Any], temperature: float = 0.0
    ) -> dict[str, Any]:
        import re

        task = re.search(r"<task>(\w+)</task>", user)
        name = task.group(1) if task else ""
        self.tasks.append(name)
        message = re.search(r"<customer_message>\n(.*?)\n</customer_message>", user, re.S)
        if name == "query":
            return {"query": message.group(1) if message else ""}
        if name == "requery":
            return {"query": self.requery or (message.group(1) if message else "")}
        passages = re.findall(r"^\[([^\]]+)\] (.*)$", user.split("<passages>")[1], re.M)
        if not passages:
            return {"answerable": False, "answer": "", "citations": [], "missing": "nothing was found"}
        return {"answerable": True, "answer": passages[0][1], "citations": [passages[0][0]], "missing": ""}
