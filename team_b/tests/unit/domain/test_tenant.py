import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from team_b.domain.tenant import (
    IntentSpec,
    TenantConfig,
    TenantConfigError,
    TenantRegistry,
    UnknownTenantError,
)
from team_b.domain.understanding import Locale


def config(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tenant_id": "shop_001",
        "display_name": "Nile Style",
        "default_locale": "ar",
        "history_max_turns": 12,
        "order_id_pattern": r"NS-\d{4,6}",
        "order_id_prefix": "NS-",
        "identity": {"verify_tool": "verify_customer", "required_slots": ["order_id", "phone"], "max_attempts": 2},
        "escalation": {
            "max_tool_failures": 2,
            "max_clarifications": 2,
            "min_intent_confidence": 0.55,
            "escalate_on_deny": True,
            "escalate_on_high_frustration": True,
        },
        "permissions": {
            "allowed_tools": ["get_order", "create_refund"],
            "max_risk": "high",
            "confirm_operation_kinds": ["create", "update", "delete"],
        },
        "intents": {
            "return_policy": {
                "kind": "knowledge",
                "description": "Questions about returns",
                "examples": ["can I return this", "ازاي ارجع المنتج"],
                "knowledge_query": "return policy",
            },
            "order_status": {"kind": "lookup", "lookup_tool": "get_order", "required_slots": ["order_id"]},
            "refund": {
                "kind": "action",
                "lookup_tool": "get_order",
                "action_tool": "create_refund",
                "required_slots": ["order_id"],
                "argument_map": {"order_id": "order_id", "amount": "order_total"},
            },
            "talk_to_human": {"kind": "handoff"},
            "greeting": {"kind": "smalltalk"},
        },
    }
    return {**base, **over}


def test_full_tenant_config_parses() -> None:
    t = TenantConfig.model_validate(config())
    assert t.display_name == "Nile Style" and t.default_locale is Locale.AR
    assert t.identity.verify_tool == "verify_customer" and t.identity.required_slots == ("order_id", "phone")
    assert t.intents["refund"].action_tool == "create_refund"
    assert t.intents["refund"].argument_map["amount"] == "order_total"
    assert t.permissions.max_risk == "high"


def test_defaults_are_fail_closed() -> None:
    t = TenantConfig.model_validate(
        {
            "tenant_id": "t",
            "display_name": "T",
            "order_id_pattern": r"\d+",
            "identity": {"verify_tool": "verify"},
        }
    )
    assert t.permissions.allowed_tools == ()  # nothing is allowed until the shop says so
    assert t.permissions.confirm_operation_kinds == ("create", "update", "delete")
    assert t.escalation.escalate_on_deny is True and t.escalation.max_tool_failures == 2
    assert t.identity.max_attempts == 2 and t.history_max_turns == 12 and t.default_locale is Locale.EN


@pytest.mark.parametrize(
    "override",
    [
        {"tenant_id": "Nile Style"},
        {"tenant_id": ""},
        {"display_name": ""},
        {"order_id_pattern": "("},
        {"history_max_turns": 0},
        {"default_locale": "mixed"},
        {"identity": {"verify_tool": "v", "max_attempts": 0}},
        {"permissions": {"max_risk": "extreme"}},
        {"surprise": 1},
    ],
)
def test_bad_values_are_rejected(override: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        TenantConfig.model_validate(config(**override))


def test_action_and_lookup_intents_need_their_tool() -> None:
    with pytest.raises(ValidationError, match="action_tool"):
        IntentSpec(kind="action")
    with pytest.raises(ValidationError, match="lookup_tool"):
        IntentSpec(kind="lookup")
    assert IntentSpec(kind="knowledge").lookup_tool is None
    assert IntentSpec(kind="handoff").action_tool is None


def test_unknown_intent_kind_is_rejected() -> None:
    with pytest.raises(ValidationError):
        IntentSpec.model_validate({"kind": "dance"})


def write(directory: Path, name: str, data: dict[str, Any]) -> Path:
    path = directory / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_registry_loads_every_json_file_in_the_directory(tmp_path: Path) -> None:
    write(tmp_path, "shop_001.json", config())
    write(tmp_path, "telecom_001.json", config(tenant_id="telecom_001", display_name="Telecom"))
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")
    registry = TenantRegistry.from_dir(tmp_path)
    assert registry.tenant_ids() == ("shop_001", "telecom_001")
    assert len(registry) == 2 and "shop_001" in registry and "nope" not in registry
    assert registry.get("shop_001").display_name == "Nile Style"
    assert {t.tenant_id for t in registry} == {"shop_001", "telecom_001"}


def test_registry_reads_arabic_text_and_tolerates_a_bom(tmp_path: Path) -> None:
    data = config()
    data["display_name"] = "نايل ستايل"
    (tmp_path / "shop_001.json").write_bytes(b"\xef\xbb\xbf" + json.dumps(data, ensure_ascii=False).encode("utf-8"))
    assert TenantRegistry.from_dir(tmp_path).get("shop_001").display_name == "نايل ستايل"


def test_unknown_tenant_raises(tmp_path: Path) -> None:
    write(tmp_path, "shop_001.json", config())
    with pytest.raises(UnknownTenantError):
        TenantRegistry.from_dir(tmp_path).get("other")


def test_registry_names_the_broken_file(tmp_path: Path) -> None:
    write(tmp_path, "shop_001.json", config())
    write(tmp_path, "bad_001.json", config(tenant_id="bad_001", order_id_pattern="("))
    with pytest.raises(TenantConfigError, match="bad_001.json"):
        TenantRegistry.from_dir(tmp_path)


def test_registry_rejects_invalid_json_and_name_mismatch_and_missing_dir(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(TenantConfigError, match="broken.json"):
        TenantRegistry.from_dir(tmp_path)
    (tmp_path / "broken.json").unlink()
    write(tmp_path, "other_name.json", config())
    with pytest.raises(TenantConfigError, match="must match the file name"):
        TenantRegistry.from_dir(tmp_path)
    with pytest.raises(TenantConfigError, match="not found"):
        TenantRegistry.from_dir(tmp_path / "missing")


def test_empty_directory_gives_an_empty_registry(tmp_path: Path) -> None:
    assert len(TenantRegistry.from_dir(tmp_path)) == 0
