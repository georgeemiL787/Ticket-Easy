from ..config import AppError
from ..progress import check_cancelled, live_step
from .base import Provider
from .budget import base_kind
from .openai_compat import chat_completion, json_schema_format
from .schemas import groq_schema
from .types import ProviderResponse

# Reasoning tokens count toward max_tokens: room for a whole proposal answer (OpenRouter's 6000) plus low-effort reasoning.
GROQ_MAX_TOKENS = 8192


class GroqProvider(Provider):
    name = "groq"

    def __init__(self, settings, store, transport=None, api_key=None):
        super().__init__(settings, store, transport)
        self.api_key = settings.groq_api_key if api_key is None else api_key

    @classmethod
    def check_configuration(cls, settings):
        super().check_configuration(settings)
        if not settings.groq_api_keys:
            raise AppError("model_configuration", "Missing Groq API key", 503)

    def is_service_failure(self, response):
        if super().is_service_failure(response):
            return True
        if response.status_code != 413:
            return False
        # Groq also uses 413 for requests exceeding the credential's TPM allowance.
        # Only its explicit rate-limit code is eligible; an ordinary 413 still stops.
        try:
            response.read()
            error = response.json().get("error", {})
        except (ValueError, AttributeError):
            return False
        return isinstance(error, dict) and error.get("code") == "rate_limit_exceeded"

    def complete(self, kind, messages, schema, run_id, fixing=False):
        check_cancelled()
        effort = self.settings.groq_reasoning_effort
        # include_reasoning=false keeps reasoning text out of the answer; both parameters are rejected by non-reasoning models.
        reasoning = dict(reasoning_effort=effort, include_reasoning=False) if effort else {}
        body = dict(model=self.model, messages=messages, stream=False, temperature=0, max_tokens=GROQ_MAX_TOKENS, response_format=json_schema_format(groq_schema(schema)), **reasoning)
        step = live_step(base_kind(kind), self.name, False, retry=False, fixing=fixing)
        content, value = chat_completion(self, body, self.api_key, step)
        return ProviderResponse(content, value.get("usage") or {})
