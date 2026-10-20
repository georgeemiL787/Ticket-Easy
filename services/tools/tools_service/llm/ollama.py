import json
import time
import httpx
from ..config import AppError
from ..progress import check_cancelled, live_step
from .base import Provider
from .budget import OLLAMA_MARGIN_TOKENS, THINK_KINDS, base_kind, num_predict, ollama_budget
from .types import ProviderResponse

USAGE_KEYS = ("prompt_eval_count", "eval_count", "done_reason", "total_duration", "prompt_eval_duration", "eval_duration")


def ollama_stream(response, deadline, step):
    """Read Ollama's NDJSON stream: thinking goes only to the live step; answer content is joined."""
    parts, value = [], {}
    for line in response.iter_lines():
        check_cancelled()
        if not line.strip():
            continue
        value = json.loads(line)
        if "error" in value:
            return value
        message = value.get("message") or {}
        if step is not None:
            step["thinking"] += message.get("thinking") or ""
            step["answer_tokens"] += 1 if message.get("content") else 0
        parts.append(message.get("content") or "")
        if time.monotonic() > deadline:
            raise httpx.ReadTimeout("OLLAMA_TIMEOUT reached")
    if step is not None:
        step["finished"] = time.time()
    return dict(value, message=dict(value.get("message") or {}, content="".join(parts)))


class OllamaProvider(Provider):
    name = "ollama"

    def thinks(self, kind):
        return self.settings.ollama_think or base_kind(kind) in THINK_KINDS

    def prompt_tokens(self, messages, think):
        """Exact prompt tokens from a one-token Ollama call with the same messages and context. Ollama cuts a prompt
        longer than num_ctx and reports a count near num_ctx, which the budget then rejects; nothing cut is ever used."""
        check_cancelled()
        s = self.settings
        body = dict(model=s.ollama_model, messages=messages, stream=False, think=think, options={"temperature": 0, "num_ctx": s.ollama_context, "num_predict": 1})
        try:
            with httpx.Client(timeout=s.ollama_timeout, transport=self.transport, follow_redirects=False, trust_env=False) as client:
                response = client.post(s.ollama_base_url.rstrip("/") + "/api/chat", json=body)
            response.raise_for_status()
            return int(response.json()["prompt_eval_count"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise AppError("provider_service_failure", f"ollama could not count the input tokens ({type(exc).__name__})", 502)

    def check_budget(self, kind, messages, run_id):
        """Reject rather than let Ollama truncate the prompt or shift context during generation.
        Runs only when Ollama is the provider being attempted, so an unreachable fallback never blocks the primary."""
        reserve = num_predict(kind)
        budget = ollama_budget(messages, self.settings.ollama_context, reserve)
        if not budget["fits"]:
            # The byte bound overestimates tokens about fourfold; ask Ollama for the real count before rejecting.
            measured = self.prompt_tokens(messages, self.thinks(kind))
            budget.update(measured_input_tokens=measured, fits=measured + reserve + OLLAMA_MARGIN_TOKENS <= self.settings.ollama_context)
        self.store.diagnostic(run_id, "context_budget", budget)
        if not budget["fits"]:
            raise AppError("model_input_limit", f'Model input needs {budget["measured_input_tokens"]} tokens (counted by Ollama) plus {reserve} reserved output and {OLLAMA_MARGIN_TOKENS} margin, above OLLAMA_CONTEXT={budget["num_ctx"]}; reduce the spec or explicitly increase OLLAMA_CONTEXT', details={"context_budget": budget})

    def complete(self, kind, messages, schema, run_id, fixing=False):
        self.check_budget(kind, messages, run_id)
        think = self.thinks(kind)
        deadline = time.monotonic() + self.timeout
        # Thinking shares num_predict with the answer: if it runs out, the same input is sent once without thinking.
        for think_now in ((True, False) if think else (False,)):
            check_cancelled()
            body = dict(model=self.model, messages=messages, stream=True, format=schema, think=think_now, options={"temperature": 0, "num_ctx": self.settings.ollama_context, "num_predict": num_predict(kind)})
            step = live_step(base_kind(kind), self.name, think_now, retry=think and not think_now, fixing=fixing)
            value = self.post(self.base_url + "/api/chat", body, {}, lambda response: ollama_stream(response, deadline, step))
            if think_now and "error" not in value and value.get("done_reason") == "length":
                self.store.diagnostic(run_id, "thinking_retry", dict(eval_count=value.get("eval_count"), reason="output limit reached while thinking"))
                continue
            break
        if "error" in value:
            raise AppError("provider_request_failure", f"{self.name} returned an error response", 502)
        raw = value["message"]["content"]
        if value.get("done_reason") == "length":
            raise ValueError("Output was truncated")
        return ProviderResponse(raw, {k: value.get(k) for k in USAGE_KEYS})
