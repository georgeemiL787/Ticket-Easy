import json
import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaValidationError
from pydantic import ValidationError
from ..config import AppError
from ..diagnostics import response_diagnostic, output_diagnostic
from .groq import GroqProvider
from .ollama import OllamaProvider
from .openrouter import OpenRouterProvider
from .prompts import model_messages
from .schemas import drop_echoes, output_schema
from .types import AttemptInfo

PROVIDERS = {p.name: p for p in (OllamaProvider, OpenRouterProvider, GroqProvider)}


class Providers:
    def __init__(self, settings, store, transport=None):
        self.settings, self.store, self.transport = settings, store, transport

    def provider(self, name, api_key=None):
        if name == "groq":
            return GroqProvider(self.settings, self.store, self.transport, api_key=api_key)
        return PROVIDERS[name](self.settings, self.store, self.transport)

    def configured_chain(self):
        s = self.settings
        chain = s.llm_chain
        if any(p not in PROVIDERS for p in chain) or len(set(chain)) != len(chain):
            raise AppError("model_configuration", "Select distinct supported providers; fallback may be none or a comma-separated list", 503)
        for p in chain:
            PROVIDERS[p].check_configuration(s)
        return chain

    def ollama_prompt_tokens(self, messages, think):
        return self.provider("ollama").prompt_tokens(messages, think)

    def check_ollama_budget(self, kind, messages, run_id):
        return self.provider("ollama").check_budget(kind, messages, run_id)

    def call(self, kind, payload, output_model, run_id):
        chain = self.configured_chain()
        schema, echoes = output_schema(kind, payload, output_model)
        data, messages = model_messages(kind, payload)
        if len(data) > self.settings.max_model_chars:
            raise AppError("model_input_limit", "Model input exceeds configured limit; no content was truncated")
        failures = []
        # Start afresh on every call: a rate-limited key is never permanently disabled.
        attempts = [(name, key) for name in chain for key in (self.settings.groq_api_keys if name == "groq" else [None])]
        for name, api_key in attempts:
            provider = self.provider(name, api_key)
            model = provider.model
            try:
                response = provider.complete(kind, messages, schema, run_id, fixing="grounding_feedback" in payload)
                secrets = self.settings.model_secrets
                self.store.diagnostic(run_id, "model_response", dict(provider=name, model=model, **response_diagnostic(response.raw, secrets), **({"usage": response.usage} if response.usage else {})))
                if name == "groq":
                    # The Groq-compatible wire schema omits correlations its compiler cannot express.
                    # Validate the full original contract before removing echoes or accepting a proposal.
                    Draft202012Validator(schema).validate(json.loads(response.raw))
                result = output_model.model_validate(drop_echoes(json.loads(response.raw), echoes)) if echoes else output_model.model_validate_json(response.raw)
                self.store.diagnostic(run_id, "parsed_output", output_diagnostic(result.model_dump(), secrets))
                self.store.attempt(run_id, name, model, "succeeded")
                return result
            except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
                error = AppError("provider_service_failure", f"{name} connection/timeout failure ({type(exc).__name__})", 502)
            except AppError as exc:
                error = exc
            except (ValueError, KeyError, IndexError, TypeError, ValidationError, SchemaValidationError) as exc:
                error = AppError("invalid_model_output", f"{name} returned invalid structured output ({type(exc).__name__})", 502)
            except httpx.HTTPError as exc:
                error = AppError("provider_request_failure", f"{name} request failed ({type(exc).__name__})", 502)
            info = AttemptInfo(code=error.code, message=error.message, provider=name)
            self.store.attempt(run_id, name, model, "failed", info)
            failures.append(info)
            # Only an unavailable service falls back; invalid output, rejected requests and cancellation stop here.
            if error.code != "provider_service_failure":
                raise error
        raise AppError("providers_failed", "All selected provider attempts failed", 502, {"attempts": failures})
