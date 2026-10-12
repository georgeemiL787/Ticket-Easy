"""Web layer contracts: import paths, error shapes, request guards, form dispatch and the background-job registry."""
import asyncio
import importlib
import re
import time
import pytest
from team_c import jobs
from team_c.config import AppError


def csrf(client):
    return re.search(r'name="csrf" value="([^"]+)"', client.get("/").text).group(1)


def test_create_app_import_paths():
    import team_c.app
    import team_c.web
    from team_c.web import create_app, field_kind, form_arguments
    assert create_app is team_c.app.create_app
    for target in ("team_c.web:create_app", "team_c.app:create_app"):
        module, attr = target.split(":")
        assert getattr(importlib.import_module(module), attr) is create_app
    assert field_kind({"type": ["null", "integer"]}) == "integer"
    assert form_arguments({"properties": {"n": {"type": "integer"}, "x": {"type": "string"}}}, {"a:n": "3", "a:x": ""}) == {"n": 3}


def test_errors_are_json_for_the_api_and_a_page_otherwise(env):
    _, client, _ = env
    r = client.get("/api/v1/proposals/unknown")
    assert r.status_code == 404 and r.json() == dict(code="not_found", message="Record not found", details={})
    r = client.get("/businesses/unknown")
    assert r.status_code == 404 and "Unable to complete this step" in r.text and "Record not found" in r.text
    r = client.post("/api/v1/businesses/b/local-projects")
    assert r.status_code == 410 and r.json()["code"] == "code_discovery_retired"


def test_request_guards(env):
    _, client, _ = env
    assert client.get("/health", headers={"host": "evil.invalid"}).status_code == 400
    token = csrf(client)
    r = client.post("/actions/business", data=dict(csrf=token, name="x", description="y"), headers={"origin": "https://evil.invalid"})
    assert r.status_code == 403 and r.json()["code"] == "origin_rejected"
    r = client.post("/actions/business", data=dict(csrf="wrong", name="x", description="y"))
    assert r.status_code == 403 and "Reload the page and try again" in r.text
    r = client.post("/actions/business", data=dict(csrf=token, name="Same origin", description="y"), headers={"origin": "http://testserver"})
    assert r.status_code == 200 and r.url.path.startswith("/businesses/")


def test_form_dispatch_for_retired_and_unknown_actions(env):
    _, client, _ = env
    token = csrf(client)
    r = client.post("/actions/local_project", data=dict(csrf=token))
    assert r.status_code == 410 and "Local-code discovery is retired" in r.text
    r = client.post("/actions/nonsense", data=dict(csrf=token, proposal_id="p", version="1", revision="0"))
    assert r.status_code == 404 and "Unknown action" in r.text


def test_job_registry_runs_one_job_and_expires_old_ones(monkeypatch):
    monkeypatch.setattr(jobs, "LIVE_WAIT_SECONDS", 5)
    registry = {"old": dict(id="old", action="suggest", status="done", started=time.time() - jobs.EXPIRY_SECONDS - 1)}
    assert asyncio.run(jobs.start(registry, "suggest", "http://testserver/businesses/b?x=1", lambda: "/businesses/b")) == "/businesses/b"
    assert "old" not in registry and len(registry) == 1
    job = next(iter(registry.values()))
    assert job["status"] == "done" and job["back"] == "/businesses/b?x=1"
    job["status"] = "running"
    called = []
    assert asyncio.run(jobs.start(registry, "reconcile", "", lambda: called.append(1))) == f"/live/{job['id']}?busy=1" and not called
    assert jobs.banner(registry)["title"] == "Looking for useful tools"
    jobs.cancel(registry, job["id"])
    assert job["cancelled"]


def test_failed_job_raises_its_error_and_unknown_jobs_have_expired(monkeypatch):
    monkeypatch.setattr(jobs, "LIVE_WAIT_SECONDS", 5)
    def fail():
        raise AppError("cancelled", "Stopped by the owner", 409)
    registry = {}
    with pytest.raises(AppError) as error:
        asyncio.run(jobs.start(registry, "suggest", "not-a-path", fail))
    job = next(iter(registry.values()))
    assert error.value.code == "cancelled" and job["status"] == "failed" and job["back"] == "/"
    with pytest.raises(AppError) as missing:
        jobs.live_view(registry, "unknown", None)
    assert missing.value.status == 404
