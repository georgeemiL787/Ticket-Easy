"""Opt-in deterministic check against the owner's read-only clone. No model calls."""
import hashlib
import os
from pathlib import Path
import pytest
from team_c.config import Settings
from team_c.code_discovery import index_project, discover_project

TARGET = Path(os.environ.get("TARGET_SYSTEM_PATH", r"D:\target-system"))
pytestmark = pytest.mark.skipif(not (TARGET / "backend/app/api/deps.py").is_file(), reason="target clone not available")


def tree_hashes():
    return {p.relative_to(TARGET).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in (TARGET / "backend").rglob("*.py")}


def test_target_discovery_resolution_and_exposure_without_changing_source():
    before = tree_hashes()
    settings = Settings(_env_file=None, allowed_project_directory=str(TARGET))
    indexed = index_project(str(TARGET), settings)
    assert "backend/app/api/deps.py" in indexed["files"]
    assert any(d["code"] == "python_grammar_adapted" and d["pointer"] == "backend/app/api/deps.py:36" for d in indexed["diagnostics"])

    plain = discover_project(indexed, "t", settings)
    assert plain["discovery_summary"]["detected_routes"] == 23 and plain["discovery_summary"]["eligible_for_proposal"] == 0
    assert all(o["path"] is None for o in plain["operations"])
    assert plain["route_settings"][0]["setting"] == "API_V1_STR" and plain["route_settings"][0]["value_source"] == "unconfirmed"

    inv = discover_project(indexed, "t", settings, {"API_V1_STR": "/api/v1"})
    ops = {(o["source_pointer"].split("/")[-1].split(":")[0], o["summary"]): o for o in inv["operations"]}
    read, create = ops[("items.py", "read_item")], ops[("items.py", "create_item")]
    assert (read["method"], read["path"], read["supported"]) == ("GET", "/api/v1/items/{id}", True)
    assert (create["method"], create["path"], create["supported"]) == ("POST", "/api/v1/items/", True)
    assert create["inputs"]["body.title"]["required"] and not create["inputs"]["body.description"]["required"]
    assert {d["kind"] for d in read["dependencies"]} == {"database_session", "authenticated_user", "security_scheme"}
    assert read["access"]["ownership_checks"] and read["access"]["runtime_verified"] is False
    assert ops[("login.py", "login_access_token")]["exposure"]["status"] == "restricted"
    assert ops[("users.py", "read_users")]["exposure"]["status"] == "restricted"
    private = ops[("private.py", "create_user")]
    assert private["path"] is None and private["discovery"]["route"]["registration"] == "conditional"
    assert inv["discovery_summary"]["eligible_for_proposal"] == 8
    assert tree_hashes() == before
