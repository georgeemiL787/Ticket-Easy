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


# Thinking shares the num_predict output budget with the answer; area assignment is plain sorting and does not think.
THINK_KINDS = {"request_triage", "suggestion", "area_naming", "generation", "revision", "reconciliation"}


def input_fits(settings, kind, payload):
    """Size batches for the primary provider; fallbacks enforce their own limits when attempted."""
    data, messages = model_messages(kind, payload)
    if len(data) > settings.max_model_chars:
        return False
    return settings.llm_primary != "ollama" or ollama_budget(messages, settings.ollama_context, num_predict(kind))["fits"]
