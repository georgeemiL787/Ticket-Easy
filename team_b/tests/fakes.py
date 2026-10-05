"""Test doubles shared by the unit tests."""

from typing import Any


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
