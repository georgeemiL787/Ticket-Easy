"""Credentials of managed connectors are encrypted at rest and never appear in an API response or a log line."""
import json
import logging
import sqlite3

import pytest

from team_c.config import AppError, Settings
from team_c.connectors import load_connectors, managed_connectors
from team_c.persistence.util import dump, now
from team_c.secretbox import PREFIX, has_plaintext, open_value, seal, seal_value, unseal

TOKEN, PASSWORD = "tok-PLAINTEXT-123456", "pw-PLAINTEXT-654321"


def settings_for(tmp_path, **extra):
    return Settings(_env_file=None, database_path=str(tmp_path / "t.db"), session_secret="s", **extra)


def content(business_id="b"):
    return dict(business_id=business_id, base_url="http://x.test", sandbox=True, context_fields=[], confirmed_by="owner",
                identities=dict(a=dict(scope="end_user", token=TOKEN, context={"k": "v"}), b=dict(scope="end_user", username="bob", password=PASSWORD)))


def test_values_round_trip_and_are_not_readable(tmp_path):
    settings = settings_for(tmp_path)
    sealed = seal_value(TOKEN, settings)
    assert sealed.startswith(PREFIX) and TOKEN not in sealed
    assert open_value(sealed, settings) == TOKEN
    assert seal_value(sealed, settings) == sealed  # already sealed: unchanged
    assert open_value("plain", settings) == "plain"  # an old plaintext value still reads
    assert seal_value("", settings) == ""


def test_a_whole_connector_is_sealed_and_unsealed_without_touching_other_fields(tmp_path):
    settings = settings_for(tmp_path)
    original = content()
    sealed = seal(original, settings)
    assert sealed["identities"]["a"]["token"].startswith(PREFIX) and sealed["identities"]["b"]["password"].startswith(PREFIX)
    assert sealed["identities"]["b"]["username"] == "bob" and sealed["identities"]["a"]["context"] == {"k": "v"}
    assert not has_plaintext(sealed) and has_plaintext(original)
    assert unseal(sealed, settings) == original
    assert original["identities"]["a"]["token"] == TOKEN  # the input is not changed


def test_the_key_file_is_created_beside_the_database_and_reused(tmp_path):
    settings = settings_for(tmp_path)
    sealed = seal_value(TOKEN, settings)
    key = (tmp_path / "secret.key").read_text(encoding="utf-8").strip()
    assert len(key) == 44 and key not in sealed
    assert open_value(sealed, settings_for(tmp_path)) == TOKEN  # a new process finds the same key


def test_the_key_can_come_from_the_environment_or_a_file(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("MY_TEAM_C_KEY", key)
    from_env = settings_for(tmp_path, secret_key="env:MY_TEAM_C_KEY")
    sealed = seal_value(TOKEN, from_env)
    assert open_value(sealed, settings_for(tmp_path, secret_key=key)) == TOKEN
    keyfile = tmp_path / "k.txt"
    keyfile.write_text(key, encoding="utf-8")
    assert open_value(sealed, settings_for(tmp_path, secret_key=f"file:{keyfile}")) == TOKEN
    assert not (tmp_path / "secret.key").exists()  # a configured key never creates a file
    with pytest.raises(AppError) as error:
        seal_value(TOKEN, settings_for(tmp_path, secret_key="env:NOT_SET_ANYWHERE"))
    assert error.value.code == "secret_key_missing"


def test_a_wrong_key_is_reported_not_guessed(tmp_path):
    sealed = seal_value(TOKEN, settings_for(tmp_path))
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(AppError) as error:
        open_value(sealed, settings_for(other))
    assert error.value.code in ("secret_key_wrong", "secret_key_missing")
    assert TOKEN not in error.value.message


def test_plaintext_rows_are_sealed_at_start_up_and_still_work(env):
    app, client, settings = env
    service = app.state.service
    business = client.post("/api/v1/businesses", json=dict(name="Biz", description="A business")).json()
    with service.store.connect(write=True) as conn:  # a row written before encryption existed
        conn.execute("INSERT INTO sandbox_connectors(id,business_id,content,created_at) VALUES(?,?,?,?)", ("sandbox-old", business["id"], dump(content()), now()))
    assert TOKEN.encode() in open(settings.database_path, "rb").read()
    service.connectors.seal_existing()
    with sqlite3.connect(settings.database_path) as db:
        stored = db.execute("SELECT content FROM sandbox_connectors WHERE id='sandbox-old'").fetchone()[0]
    assert TOKEN not in stored and PASSWORD not in stored and PREFIX in stored
    assert load_connectors(settings)["sandbox-old"]["identities"]["a"]["token"] == TOKEN  # the executor still gets the plain value
    assert managed_connectors(settings)["sandbox-old"]["identities"]["b"]["password"] == PASSWORD


def test_credentials_never_appear_in_responses_or_logs(env, caplog):
    app, client, settings = env
    service = app.state.service
    business = client.post("/api/v1/businesses", json=dict(name="Biz", description="A business")).json()
    with service.store.connect(write=True) as conn:
        conn.execute("INSERT INTO sandbox_connectors(id,business_id,content,created_at) VALUES(?,?,?,?)", ("sandbox-old", business["id"], dump(seal(content(business["id"]), settings)), now()))
    caplog.set_level(logging.DEBUG)
    listing = client.get(f"/api/v1/businesses/{business['id']}/connectors")
    assert listing.status_code == 200
    body = json.dumps(listing.json())
    assert TOKEN not in body and PASSWORD not in body and PREFIX not in body
    assert [i["name"] for i in listing.json()[0]["identities"]] == ["a", "b"]  # names and account types only
    check = client.post("/api/v1/connectors/sandbox-old/check")
    assert TOKEN not in check.text and PASSWORD not in check.text
    assert TOKEN not in caplog.text and PASSWORD not in caplog.text
