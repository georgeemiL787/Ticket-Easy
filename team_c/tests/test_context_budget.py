"""Ollama context budget: input-token upper bound + reserved output + margin, all in tokens."""
import json
import httpx
import pytest
from team_c.config import AppError, Settings
from team_c.models import AreaAssignmentOutput, AreaNamingOutput, GenerationOutput
from team_c.providers import AREA_SYSTEM, GENERATION_NUM_PREDICT, OLLAMA_MARGIN_TOKENS, OLLAMA_NUM_PREDICT, OLLAMA_TEMPLATE_BYTES, SYSTEM, THINK_KINDS, Providers, num_predict
from team_c.storage import Store, now, uid

# Quotes and newlines are escaped again in the HTTP JSON body but are single bytes of prompt text.
PAYLOAD = dict(business=dict(description='quoted "text"\n' * 400))


def input_bound(payload, kind="generation"):
    user = f"Task: {kind}\nUNTRUSTED_DATA\n{json.dumps(payload, ensure_ascii=False)}\nEND_UNTRUSTED_DATA"
    return len(SYSTEM.encode()) + len(user.encode()) + OLLAMA_TEMPLATE_BYTES


def call(tmp_path, context, kind="generation", payload=PAYLOAD, output_model=GenerationOutput, content=dict(proposals=[], capability_gaps=[]), measured=None):
    settings = Settings(_env_file=None, database_path=str(tmp_path / "b.db"), ollama_context=context, llm_fallback="none")
    store = Store(settings.database_path)
    bid = uid()
    with store.connect(write=True) as c:
        c.execute("INSERT INTO businesses VALUES(?,?,?,?)", (bid, "B", "B", now()))
    sent = []
    def handler(request):
        sent.append(request.content)
        if json.loads(request.content)["options"]["num_predict"] == 1:
            assert measured is not None, "the byte bound fits; no token count is needed"
            return httpx.Response(200, json={"message": {"content": ""}, "done": True, "done_reason": "length", "prompt_eval_count": measured, "eval_count": 1})
        return httpx.Response(200, json={"message": {"content": json.dumps(content)}, "done": True, "done_reason": "stop", "prompt_eval_count": 1, "eval_count": 1})
    run = store.start_run(bid, kind, {})
    providers = Providers(settings, store, httpx.MockTransport(handler))
    try:
        providers.call(kind, payload, output_model, run)
        error = None
    except AppError as exc:
        error = exc
    budget = json.loads(store.all("SELECT payload FROM diagnostics WHERE run_id=? AND stage='context_budget'", (run,))[0]["payload"])
    return sent, error, budget


def test_fitting_request_is_sent_with_the_configured_num_ctx(tmp_path):
    context = input_bound(PAYLOAD) + GENERATION_NUM_PREDICT + OLLAMA_MARGIN_TOKENS
    sent, error, budget = call(tmp_path, context)
    assert error is None and len(sent) == 1
    body = json.loads(sent[0])
    # Proposal writing thinks, so it reserves a larger output budget than the other kinds.
    assert body["options"]["num_ctx"] == context and body["options"]["num_predict"] == GENERATION_NUM_PREDICT == 16384 and body["think"] is True
    assert budget == dict(input_tokens_upper_bound=input_bound(PAYLOAD), reserved_output_tokens=GENERATION_NUM_PREDICT, margin_tokens=OLLAMA_MARGIN_TOKENS, num_ctx=context, fits=True)
    # The HTTP body (JSON escaping plus the decoding-grammar schema) exceeds the input bound, yet it is not prompt text.
    assert len(sent[0]) > input_bound(PAYLOAD)


def test_revision_and_reconciliation_think_with_the_proposal_reserve_and_repair_does_not(tmp_path):
    for kind, thinks in (("revision: produce exactly one replacement or capability gaps", True),
                         ("repair: return outcome revised with the complete corrected proposal, or cannot_repair, or capability_gap", False)):
        sent, _, _ = call(tmp_path / kind.split(":")[0], 200_000, kind)
        body = json.loads(sent[0])
        assert body["think"] is thinks and body["options"]["num_predict"] == (GENERATION_NUM_PREDICT if thinks else OLLAMA_NUM_PREDICT), kind
    assert "reconciliation" in THINK_KINDS and num_predict("reconciliation") == GENERATION_NUM_PREDICT


def test_area_naming_thinks_with_its_short_prompt(tmp_path):
    payload = dict(business=dict(name="B", description="B"), groups=[dict(key="bookings", label="Bookings")])
    area = dict(name="Bookings", description="d", audience="customer", reason="TEST-ONLY")
    sent, error, _ = call(tmp_path, 32768, "area_naming", payload, AreaNamingOutput, dict(areas=[area]))
    body = json.loads(sent[0])
    assert error is None and body["think"] is True and body["stream"] is True and body["messages"][0]["content"] == AREA_SYSTEM
    assert body["options"]["num_predict"] == OLLAMA_NUM_PREDICT == 8192


def test_area_assignment_format_requires_each_group_once_with_a_named_area(tmp_path):
    payload = dict(business=dict(name="B", description="B"), areas=[dict(name="Care", description="d"), dict(name="Admin", description="d")],
                   groups=[dict(key="patients", label="Patients"), dict(key="backup", label="Backup")])
    sent, error, _ = call(tmp_path, 32768, "area_assignment", payload, AreaAssignmentOutput, dict(assignments=dict(patients="Care", backup="Admin")))
    body = json.loads(sent[0])
    shape = body["format"]["properties"]["assignments"]
    assert error is None and body["think"] is False and shape["required"] == ["patients", "backup"] and shape["additionalProperties"] is False
    assert shape["properties"]["backup"]["enum"] == ["Care", "Admin"]


def test_byte_bound_overflow_is_counted_by_ollama_and_rejected_only_if_the_real_count_does_not_fit(tmp_path):
    context = input_bound(PAYLOAD) + GENERATION_NUM_PREDICT + OLLAMA_MARGIN_TOKENS - 1
    limit = context - GENERATION_NUM_PREDICT - OLLAMA_MARGIN_TOKENS
    sent, error, budget = call(tmp_path, context, measured=limit + 1)
    count = json.loads(sent[0])
    assert len(sent) == 1 and count["options"] == dict(temperature=0, num_ctx=context, num_predict=1) and count["stream"] is False
    assert error.code == "model_input_limit" and "counted by Ollama" in error.message and not budget["fits"] and budget["measured_input_tokens"] == limit + 1
    assert error.details["context_budget"] == budget
    # The real count fitting exactly is accepted: the same messages are then sent with the full output reserve.
    sent, error, budget = call(tmp_path / "fits", context, measured=limit)
    assert error is None and len(sent) == 2 and budget["fits"] and budget["input_tokens_upper_bound"] == input_bound(PAYLOAD)
    first, second = json.loads(sent[0]), json.loads(sent[1])
    assert first["messages"] == second["messages"] and second["options"]["num_predict"] == GENERATION_NUM_PREDICT


def test_output_reserve_is_enforced_even_when_the_input_alone_fits(tmp_path):
    context = input_bound(PAYLOAD) + 100
    sent, error, _ = call(tmp_path, context, measured=context - GENERATION_NUM_PREDICT)
    assert len(sent) == 1 and error.code == "model_input_limit"
