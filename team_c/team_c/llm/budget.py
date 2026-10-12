from .prompts import model_messages


# Ollama applies `format` as a decoding grammar, not prompt text (the served template renders only
# system/user/assistant/tool content). Qwen's byte-level BPE emits at most one token per UTF-8 byte,
# so chat text bytes plus the template wrapper bound input tokens from above. Generated tokens,
# including thinking when enabled, are capped by num_predict, which is reserved in full.
OLLAMA_NUM_PREDICT = 8192
OLLAMA_TEMPLATE_BYTES = 128
OLLAMA_MARGIN_TOKENS = 512


# Writing, revising and reconciling a proposal think before a long answer (a whole proposal);
# both share num_predict, so they get a larger reserve.
GENERATION_NUM_PREDICT = 16384
PROPOSAL_KINDS = {"generation", "revision", "reconciliation"}


def base_kind(kind):
    """Revision and repair calls name their task after the kind, e.g. "revision: produce ..."."""
    return kind.split(":", 1)[0]


def num_predict(kind):
    return GENERATION_NUM_PREDICT if base_kind(kind) in PROPOSAL_KINDS else OLLAMA_NUM_PREDICT


def ollama_budget(messages, num_ctx, reserved=OLLAMA_NUM_PREDICT):
    input_bound = sum(len(m["content"].encode()) for m in messages) + OLLAMA_TEMPLATE_BYTES
    return dict(input_tokens_upper_bound=input_bound, reserved_output_tokens=reserved, margin_tokens=OLLAMA_MARGIN_TOKENS,
                num_ctx=num_ctx, fits=input_bound + reserved + OLLAMA_MARGIN_TOKENS <= num_ctx)


# Cloud providers count input tokens with their own tokenizer, which is not callable offline, so
# batches are sized from an estimate. Measured against real Groq responses: the fixed system prompt
# runs near 3.8 characters per token while a dense operation index runs near 1.7, so a payload is
# mixed. This factor is the weighted middle: high enough that a dense index is not sent past a
# per-request allowance, low enough that a prompt-heavy request is not mistaken for oversized and
# starved of its operations. The allowance stays the authority; this only decides how much to send.
CLOUD_CHARS_PER_TOKEN = 2.5
CLOUD_MARGIN_TOKENS = 256


def cloud_budget(messages, ceiling):
    """Bound one cloud request by its configured input allowance.

    The ceiling bounds input only, as a provider's per-request token allowance does; the margin
    covers the provider's own accounting and prompt wrapper. Output limits and the model's context
    window stay the provider's responsibility and still surface as its own error. A provider with no
    configured ceiling is not bounded here, which leaves the existing fallback behaviour untouched.

    A proposal prompt carries a large fixed system prompt before any inventory, so a small allowance
    can be exhausted by the prompt alone. `fits` is then false even for an empty operation list, and
    batch builders stop with nothing to send instead of claiming a request fits.
    """
    chars = sum(len(m["content"].encode()) for m in messages)
    tokens = -(-chars // CLOUD_CHARS_PER_TOKEN)
    return dict(input_tokens_upper_bound=tokens, input_chars=chars, margin_tokens=CLOUD_MARGIN_TOKENS,
                chars_per_token=CLOUD_CHARS_PER_TOKEN, input_tokens_allowance=ceiling,
                fits=bool(ceiling) and tokens + CLOUD_MARGIN_TOKENS <= ceiling)


# Thinking shares the num_predict output budget with the answer; area assignment is plain sorting and does not think.
THINK_KINDS = {"request_triage", "suggestion", "area_naming", "generation", "revision", "reconciliation"}


def input_ceiling(settings, provider):
    """The provider's configured input allowance; None means the provider has no separate input ceiling."""
    ceiling = getattr(settings, provider + "_input_tokens", 0)
    return ceiling or None


def input_fits(settings, kind, payload):
    """Size batches for the primary provider; fallbacks enforce their own limits when attempted."""
    data, messages = model_messages(kind, payload)
    if len(data) > settings.max_model_chars:
        return False
    primary = settings.llm_primary
    if primary == "ollama":
        return ollama_budget(messages, settings.ollama_context, num_predict(kind))["fits"]
    return cloud_budget(messages, input_ceiling(settings, primary))["fits"]
