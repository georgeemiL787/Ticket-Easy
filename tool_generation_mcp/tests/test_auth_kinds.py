"""API-key and HTTP-basic authentication: derived from the specification, sent by the executor, never reported."""
import base64

import httpx
import pytest

from team_c.artifacts import auth_for
from team_c.config import AppError
from team_c.executor import Run, credentials


def operation(scheme):
    return {"method": "GET", "path": "/orders", "declared_auth": {"status": "required", "alternatives": [[{"scheme": "s", "scopes": []}]]},
            "security_schemes": {"s": scheme}}


@pytest.mark.parametrize(("scheme", "expected"), [
    ({"type": "apiKey", "in": "header", "name": "X-Api-Key"}, {"type": "api_key", "location": "header", "name": "X-Api-Key"}),
    ({"type": "apiKey", "in": "query", "name": "key"}, {"type": "api_key", "location": "query", "name": "key"}),
    ({"type": "http", "scheme": "basic"}, {"type": "http_basic"}),
    ({"type": "http", "scheme": "Bearer"}, {"type": "http_bearer"}),
])
def test_the_supported_schemes_are_read_from_the_specification(scheme, expected):
    missing = []
    found = auth_for(operation(scheme), missing)
    assert found and {k: found[k] for k in expected} == expected and not missing


@pytest.mark.parametrize("scheme", [{"type": "apiKey", "in": "cookie", "name": "sid"}, {"type": "apiKey", "in": "header", "name": "bad name"}, {"type": "openIdConnect"}])
def test_other_schemes_are_reported_as_unsupported(scheme):
    missing = []
    assert auth_for(operation(scheme), missing) is None and missing and "no supported authentication" in missing[0]


def artifact(auth):
    return {"connector": {"auth": auth}, "proposal": {"business_id": "b"},
            "limits": {"timeout_seconds": 5, "max_response_bytes": 10_000, "max_request_bytes": 10_000}}


def run_with(auth, identity, seen):
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={})
    return Run(artifact(auth), {}, identity, "http://api.test", httpx.MockTransport(handler))


def test_an_api_key_goes_in_its_header():
    seen = []
    run = run_with({"type": "api_key", "location": "header", "name": "X-Api-Key"}, {"token": "k-123"}, seen)
    headers = run.authenticate()
    run.send("GET", "http://api.test/orders", headers=headers)
    assert seen[0].headers["x-api-key"] == "k-123"


def test_an_api_key_in_the_query_is_added_to_every_request():
    seen = []
    run = run_with({"type": "api_key", "location": "query", "name": "key"}, {"token": "k-123"}, seen)
    assert run.authenticate() == {}
    run.send("GET", "http://api.test/orders?limit=2", headers={})
    assert dict(seen[0].url.params) == {"limit": "2", "key": "k-123"}


def test_basic_authentication_sends_the_encoded_pair():
    seen = []
    run = run_with({"type": "http_basic"}, {"username": "ana", "password": "s3cret"}, seen)
    run.send("GET", "http://api.test/orders", headers=run.authenticate())
    assert seen[0].headers["authorization"] == "Basic " + base64.b64encode(b"ana:s3cret").decode()


@pytest.mark.parametrize(("auth", "identity"), [
    ({"type": "api_key", "location": "header", "name": "X-Api-Key"}, {"scope": "end_user"}),
    ({"type": "http_basic"}, {"scope": "end_user", "username": "ana"}),
])
def test_a_missing_credential_stops_before_any_request(auth, identity):
    with pytest.raises(AppError) as error:
        credentials(identity, dict(artifact(auth), access_policy=None, access_requirements=[]))
    assert error.value.code in ("credential_missing", "identity_unknown", "access_denied") or "credential" in error.value.message.lower()
