import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_service.domain.bundle import TenantBundle, secret_like_keys

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "bundles"


def load(name: str) -> dict[str, object]:
    data: dict[str, object] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


@pytest.mark.parametrize("name", ["valid_minimal.json", "valid_shop_001.json"])
def test_valid_bundles_load_and_keep_a_stable_hash(name: str) -> None:
    bundle = TenantBundle.model_validate(load(name))
    again = TenantBundle.model_validate_json(bundle.model_dump_json())
    assert bundle.content_sha256 == again.content_sha256 and len(bundle.content_sha256) == 64


def test_the_hash_ignores_version_status_and_author_but_not_the_content() -> None:
    first = TenantBundle.model_validate(load("valid_minimal.json"))
    moved = TenantBundle.model_validate({**load("valid_minimal.json"), "version": 7, "created_by": "someone"})
    assert first.content_sha256 == moved.content_sha256
    changed = load("valid_minimal.json")
    changed["profile"] = {"display_name": "Another name"}
    assert TenantBundle.model_validate(changed).content_sha256 != first.content_sha256


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("invalid_duplicate_tool.json", "unique"),
        ("invalid_secret_field.json", "api_key"),
        ("invalid_allowed_tool_missing.json", "does not list"),
        ("invalid_human_only_allowed.json", "human-only"),
        ("invalid_tenant_mismatch.json", "tenant_id"),
        ("invalid_wrong_hash.json", "content_sha256"),
        ("invalid_team_c_unbound.json", "binding"),
    ],
)
def test_invalid_bundles_are_refused(name: str, reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        TenantBundle.model_validate(load(name))


def test_a_secret_looking_field_name_is_found_anywhere() -> None:
    assert secret_like_keys({"a": [{"b": {"password": 1}}], "ok": 1}) == ["a[0].b.password"]
    assert secret_like_keys({"constants": {"Api-Key": "x"}}) == ["constants.Api-Key"]
    assert secret_like_keys({"display_name": "Nile", "tools": [{"name": "t"}]}) == []


def test_a_bundle_with_a_secret_in_a_constant_is_refused() -> None:
    data = load("valid_minimal.json")
    data["tools"] = [
        {
            "name": "verify_customer",
            "source": "team_c",
            "binding": {"business_id": "b", "proposal_id": "p", "constants": {"api_key": "sk-123"}},
        }
    ]
    with pytest.raises(ValidationError, match="secrets"):
        TenantBundle.model_validate(data)


def test_the_effective_configuration_carries_the_profile_and_names_its_version() -> None:
    data = load("valid_minimal.json")
    data["profile"] = {"display_name": "Better Name", "locales": ["en", "ar"], "default_locale": "ar"}
    bundle = TenantBundle.model_validate({**data, "version": 3})
    config = bundle.effective_config()
    assert config.display_name == "Better Name" and config.default_locale.value == "ar"
    assert config.bundle_version == 3 and config.bundle_sha256 == bundle.content_sha256
    assert "bundle_version" not in config.model_dump()  # never part of a hash or a file


def test_the_default_locale_must_be_offered() -> None:
    data = load("valid_minimal.json")
    data["profile"] = {"display_name": "X", "locales": ["en"], "default_locale": "ar"}
    with pytest.raises(ValidationError, match="default_locale"):
        TenantBundle.model_validate(data)
