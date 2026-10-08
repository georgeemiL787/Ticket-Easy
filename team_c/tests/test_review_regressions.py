"""Behavioral regressions from the project audit; no real model or business endpoint is called."""
import json
from pathlib import Path

import httpx
import pytest

from team_c.artifacts import LIMITS
from team_c.capabilities import screen, title_key
from team_c.config import AppError
from team_c.executor import Run
from team_c.llm.budget import input_fits
from team_c.llm.router import Providers
from team_c.models import SuggestionOutput
from team_c.progress import LIVE
from helpers.tool_requests import idea


def execute_path(path, bindings=(), arguments=None):
    artifact = dict(proposal=dict(business_id="test"), limits=LIMITS, access_requirements=[], connector=dict(auth=None),
                    steps=[dict(id="s1", method="GET", path=path, effect="read", parameters=list(bindings), body=None,
                                responses={"200": {"schema": {"type": "string"}}})], outputs=[], output_schema=dict(required=[]))
    sent = []
    transport = httpx.MockTransport(lambda request: sent.append(request) or httpx.Response(200, json="ok"))
    report, _ = Run(artifact, arguments or {}, {}, "http://target.test/tenant-a", transport).execute()
    return report, sent


@pytest.mark.parametrize("path", [
    "/../tenant-b/records", "/./records", "/%2e%2e/tenant-b/records", "/%252e%252e/records",
    "/%2e%2e%2frecords", "/..%5crecords", "/%255c..%255crecords", "/records?injected=yes",
    "/records#fragment", "/records\n", "/records%0a", "../records",
])
def test_unsafe_operation_paths_send_no_request(path):
    report, sent = execute_path(path)
    assert sent == []
    assert report["status"] == "failed" and report["failure"]["outcome"] == "invalid_request"


@pytest.mark.parametrize("identifier", ["../other", "%2e%2e/other", "folder/../other", "..\\other"])
def test_runtime_arguments_cannot_introduce_encoded_traversal(identifier):
    param = dict(name="id", location="path", source=dict(kind="runtime_argument", reference="id"))
    report, sent = execute_path("/records/{id}", [param], dict(id=identifier))
    assert report["status"] == "failed" and sent == []


def test_legitimate_paths_and_encoded_arguments_stay_under_the_connector():
    param = dict(name="id", location="path", source=dict(kind="runtime_argument", reference="id"))
    report, sent = execute_path("/records/{id}", [param], dict(id="حجز 1"))
    assert report["status"] == "succeeded" and len(sent) == 1
    assert sent[0].url.path == "/tenant-a/records/حجز 1"
    assert sent[0].url.raw_path.startswith(b"/tenant-a/records/%")
    assert execute_path("/records/version..2")[0]["status"] == "succeeded"


def test_distinct_arabic_titles_survive_screening_and_real_duplicates_do_not():
    output = SuggestionOutput(suggestions=[idea(t, "feasible", ["op"]) for t in ("حالة الدفع", "حالة التوصيل", "حالة الدفع")])
    kept, withheld, recognized = screen(output, [dict(id="op", status="eligible")], [], [], 5)
    assert [s["title"] for s in kept] == ["حالة الدفع", "حالة التوصيل"]
    assert len(withheld) == 1 and recognized == []
    existing = [dict(proposal_id="p", name="حالة الدفع", state="approved_to_build", operation_ids=["op"])]
    kept, _, recognized = screen(output, [dict(id="op", status="eligible")], existing, [], 5)
    assert [s["title"] for s in kept] == ["حالة التوصيل"]
    assert all(s["title"] == "حالة الدفع" for s in recognized)


def test_title_normalization_handles_unicode_and_empty_keys():
    assert title_key("ＰＡＹＭＥＮＴ Status!") == title_key("payment status")
    assert title_key("réservation") == title_key("re\u0301servation")
    output = SuggestionOutput(suggestions=[idea(t, "feasible", ["op"]) for t in ("💳", "🚚")])
    kept, withheld, recognized = screen(output, [dict(id="op", status="eligible")], [], [], 5)
    assert len(kept) == 2 and withheld == recognized == []


def inventory(env):
    app, client, settings = env
    business = app.state.service.business("TEST-ONLY", "x" * 9500)
    spec = app.state.service.upload(business["id"], "ecommerce.json", (Path(__file__).parents[1] / "examples/ecommerce.json").read_bytes())
    return app, client, settings, spec


@pytest.mark.parametrize("primary", ["groq"])
def test_cloud_generation_batch_is_independent_of_ollama_fallback(env, primary):
    app, client, settings, spec = inventory(env)
    settings.llm_primary, settings.llm_fallback = primary, "none"
    # This fixture combines a 9500-character description with the ~11k-character generation system
    # prompt, so the two operations only fit an allowance well above a real 8000-token request limit.
    settings.groq_input_tokens = 16000
    url = f'/api/v1/specifications/{spec["id"]}/generation-batch'
    baseline = client.get(url).json()
    assert len(baseline["operation_ids"]) == 2
    settings.llm_fallback = "ollama"
    assert client.get(url).json() == baseline
    settings.llm_primary, settings.llm_fallback = "ollama", "none"
    settings.ollama_context = 32768
    assert not input_fits(settings, "generation", dict(business=dict(description="x" * 9500)))


@pytest.mark.parametrize("primary", ["groq", "openrouter"])
def test_cloud_generation_batch_shrinks_to_the_primary_input_allowance(env, primary):
    """A batch must be sized to the primary's real input allowance.

    The generation prompt carries a large fixed system prompt, so an allowance that cannot even hold
    the prompt yields no operations instead of a batch that the provider would reject.
    """
    app, client, settings, spec = inventory(env)
    settings.llm_primary, settings.llm_fallback = primary, "none"
    url = f'/api/v1/specifications/{spec["id"]}/generation-batch'
    setattr(settings, primary + "_input_tokens", 16000)
    full = client.get(url).json()
    assert len(full["operation_ids"]) == 2
    # An allowance that cannot hold the fixed prompt must stop the batch, not silently send one.
    setattr(settings, primary + "_input_tokens", 10)
    assert client.get(url).status_code == 422
    assert client.get(url).json()["code"] == "model_input_limit"


@pytest.mark.parametrize("primary", ["groq"])
@pytest.mark.parametrize("response_kind", ["success", "service_failure", "timeout", "malformed"])
def test_cancellation_during_cloud_call_stops_workflow_and_fallback(env, primary, response_kind):
    app, _, settings, spec = inventory(env)
    settings.llm_primary, settings.llm_fallback = primary, "ollama"
    settings.groq_api_key = "test"
    job, sent = dict(steps=[]), []

    def handler(request):
        sent.append(request)
        job["cancelled"] = True
        if response_kind == "timeout":
            raise httpx.ReadTimeout("TEST-ONLY timeout", request=request)
        if response_kind == "service_failure":
            return httpx.Response(503)
        if response_kind == "malformed":
            return httpx.Response(200, content=b"not json")
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(dict(proposals=[], capability_gaps=[]))}}]})

    app.state.service.providers = Providers(settings, app.state.store, httpx.MockTransport(handler))
    LIVE.job = job
    try:
        with pytest.raises(AppError) as exc:
            app.state.service.generate(spec["id"])
    finally:
        LIVE.job = None
    assert exc.value.code == "cancelled" and exc.value.status == 409
    assert len(sent) == 1 and job["steps"][0]["finished"] is not None
    assert app.state.store.all("SELECT * FROM proposals") == []
    assert app.state.store.one("SELECT status FROM runs")["status"] == "failed"
    assert app.state.store.one("SELECT provider,status FROM attempts") == dict(provider=primary, status="failed")


@pytest.mark.parametrize("primary", ["groq"])
def test_cancellation_while_reading_cloud_response_body(env, primary):
    app, _, settings, spec = inventory(env)
    settings.llm_primary, settings.llm_fallback = primary, "none"
    settings.groq_api_key = "test"
    job = dict(steps=[])

    class ResponseBody(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"choices": ['
            job["cancelled"] = True
            yield json.dumps(dict(finish_reason="stop", message=dict(content='{"proposals":[],"capability_gaps":[]}'))).encode() + b"]}"

    app.state.service.providers = Providers(settings, app.state.store, httpx.MockTransport(lambda req: httpx.Response(200, stream=ResponseBody())))
    LIVE.job = job
    try:
        with pytest.raises(AppError, match="Stopped by the owner"):
            app.state.service.generate(spec["id"])
    finally:
        LIVE.job = None
    assert job["steps"][0]["finished"] is not None
    assert app.state.store.all("SELECT * FROM proposals") == []
