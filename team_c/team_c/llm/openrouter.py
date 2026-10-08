from ..config import AppError
from ..progress import check_cancelled, live_step
from .base import Provider
from .budget import base_kind
from .openai_compat import chat_completion, json_schema_format
from .types import ProviderResponse


class OpenRouterProvider(Provider):
    name = "openrouter"

    @classmethod
    def check_configuration(cls, settings):
        super().check_configuration(settings)
        if not settings.openrouter_api_key:
            raise AppError("model_configuration", "Missing OpenRouter API key", 503)

    def complete(self, kind, messages, schema, run_id, fixing=False):
        check_cancelled()
        body = dict(model=self.model, messages=messages, stream=False, temperature=0, max_tokens=6000, response_format=json_schema_format(schema), provider={"require_parameters": True, "allow_fallbacks": False})
        step = live_step(base_kind(kind), self.name, False, retry=False, fixing=fixing)
        content, value = chat_completion(self, body, self.settings.openrouter_api_key, step)
        return ProviderResponse(content, value.get("usage") or {})
