from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_service.config import PROJECT_ROOT, Settings, SettingsError, unknown_setting_warnings


def test_defaults() -> None:
    s = Settings.from_env({})
    assert s.backends == "simulated" and s.store == "memory" and s.llm == "none"
    assert s.ollama_url == "http://127.0.0.1:11434" and s.ollama_model == "qwen3:8b"
    assert s.openrouter_api_key is None and s.llm_model is None and s.fixed_today is None
    assert s.llm_timeout_s == 20.0 and s.capability_ttl_s == 60.0 and s.log_json is True
    assert s.config_dir == PROJECT_ROOT / "config" / "tenants"
    assert s.data_dir == PROJECT_ROOT / "data" and s.fixtures_dir == PROJECT_ROOT / "fixtures"
    assert (PROJECT_ROOT / "pyproject.toml").is_file()  # PROJECT_ROOT really is the agent service folder


def test_every_variable_is_read() -> None:
    s = Settings.from_env(
        {
            "AGENT_BACKENDS": "live",
            "AGENT_CONFIG_DIR": "/tmp/tenants",
            "AGENT_DATA_DIR": "/tmp/data",
            "AGENT_FIXTURES_DIR": "/tmp/fixtures",
            "AGENT_STORE": "sqlite",
            "AGENT_DB_PATH": "/tmp/db.sqlite3",
            "AGENT_LLM": "openrouter",
            "OLLAMA_URL": "http://ollama:11434",
            "AGENT_OLLAMA_MODEL": "llama3.2",
            "OPENROUTER_API_KEY": "sk-or-secret",
            "AGENT_LLM_MODEL": "some/model",
            "AGENT_LLM_TIMEOUT_S": "7.5",
            "AGENT_FIXED_TODAY": "2026-09-28",
            "AGENT_CAPABILITY_TTL_S": "5",
            "AGENT_LOG_JSON": "false",
        }
    )
    assert s.backends == "live" and s.store == "sqlite" and s.llm == "openrouter"
    assert s.config_dir == Path("/tmp/tenants") and s.data_dir == Path("/tmp/data")
    assert s.fixtures_dir == Path("/tmp/fixtures") and s.db_path == Path("/tmp/db.sqlite3")
    assert s.ollama_url == "http://ollama:11434" and s.ollama_model == "llama3.2"
    assert s.openrouter_api_key is not None and s.openrouter_api_key.get_secret_value() == "sk-or-secret"
    assert s.llm_model == "some/model" and s.llm_timeout_s == 7.5 and s.capability_ttl_s == 5.0
    assert s.fixed_today == date(2026, 9, 28) and s.log_json is False


def test_empty_and_blank_values_count_as_not_set() -> None:
    s = Settings.from_env({"AGENT_BACKENDS": "", "AGENT_STORE": "   ", "AGENT_FIXED_TODAY": ""})
    assert s.backends == "simulated" and s.store == "memory" and s.fixed_today is None


def test_values_are_trimmed() -> None:
    assert Settings.from_env({"AGENT_BACKENDS": " live "}).backends == "live"


@pytest.mark.parametrize("value", ["true", "True", "1", "yes", "on"])
def test_log_json_true_spellings(value: str) -> None:
    assert Settings.from_env({"AGENT_LOG_JSON": value}).log_json is True


@pytest.mark.parametrize("value", ["false", "0", "no", "off"])
def test_log_json_false_spellings(value: str) -> None:
    assert Settings.from_env({"AGENT_LOG_JSON": value}).log_json is False


@pytest.mark.parametrize(
    ("env", "variable"),
    [
        ({"AGENT_BACKENDS": "production"}, "AGENT_BACKENDS"),
        ({"AGENT_STORE": "redis"}, "AGENT_STORE"),
        ({"AGENT_LLM": "gpt"}, "AGENT_LLM"),
        ({"AGENT_LLM_TIMEOUT_S": "fast"}, "AGENT_LLM_TIMEOUT_S"),
        ({"AGENT_LLM_TIMEOUT_S": "0"}, "AGENT_LLM_TIMEOUT_S"),
        ({"AGENT_CAPABILITY_TTL_S": "-1"}, "AGENT_CAPABILITY_TTL_S"),
        ({"AGENT_FIXED_TODAY": "28/09/2026"}, "AGENT_FIXED_TODAY"),
        ({"AGENT_LOG_JSON": "maybe"}, "AGENT_LOG_JSON"),
    ],
)
def test_bad_values_name_the_variable(env: dict[str, str], variable: str) -> None:
    with pytest.raises(SettingsError, match=variable):
        Settings.from_env(env)


def test_all_problems_are_reported_together() -> None:
    with pytest.raises(SettingsError) as info:
        Settings.from_env({"AGENT_BACKENDS": "x", "AGENT_STORE": "y"})
    assert "AGENT_BACKENDS" in str(info.value) and "AGENT_STORE" in str(info.value)


def test_openrouter_needs_a_key() -> None:
    with pytest.raises(SettingsError, match="OPENROUTER_API_KEY"):
        Settings.from_env({"AGENT_LLM": "openrouter"})
    assert Settings.from_env({"AGENT_LLM": "openrouter", "OPENROUTER_API_KEY": "k"}).llm == "openrouter"


def test_the_api_key_never_shows_in_repr_or_dump() -> None:
    s = Settings.from_env({"OPENROUTER_API_KEY": "sk-or-very-secret"})
    assert "sk-or-very-secret" not in repr(s)
    assert "sk-or-very-secret" not in str(s)
    assert "sk-or-very-secret" not in s.model_dump_json()


def test_settings_are_frozen_and_ignore_the_process_environment_when_given_a_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_BACKENDS", "live")
    assert Settings.from_env({}).backends == "simulated"  # an explicit mapping wins over os.environ
    assert Settings.from_env().backends == "live"  # no mapping: the real environment is read
    with pytest.raises(ValidationError):
        Settings.from_env({}).backends = "live"  # type: ignore[misc]


def test_the_generic_openai_provider_needs_a_url_and_a_model_and_hides_its_key() -> None:
    with pytest.raises(SettingsError, match="AGENT_LLM_BASE_URL"):
        Settings.from_env({"AGENT_LLM": "openai"})
    s = Settings.from_env(
        {
            "AGENT_LLM": "openai",
            "AGENT_LLM_BASE_URL": "https://example.test/v1",
            "AGENT_LLM_MODEL": "m",
            "AGENT_LLM_API_KEY": "very-secret-key",
        }
    )
    assert s.llm == "openai" and s.llm_api_key is not None
    assert "very-secret-key" not in repr(s) and "very-secret-key" not in str(s.model_dump())


# ---- settings that look like ours under another name are reported, never silently ignored ----


def test_a_setting_that_ends_like_ours_but_starts_differently_is_reported() -> None:
    warnings = unknown_setting_warnings({"ACME_LLM_MODEL": "x", "ACME_STORE": "sqlite", "PATH": "/bin"})
    assert "Setting ACME_LLM_MODEL is not used. Did you mean AGENT_LLM_MODEL?" in warnings
    assert "Setting ACME_STORE is not used. Did you mean AGENT_STORE?" in warnings
    assert len(warnings) == 2


def test_our_own_shared_and_sibling_settings_are_not_reported() -> None:
    env = {
        "AGENT_LLM_MODEL": "m", "AGENT_LLM_MODEL_PLANNER": "m", "OLLAMA_URL": "http://x", "OPENROUTER_API_KEY": "k",
        "KNOWLEDGE_ADMIN_API_KEY": "a", "TOOLS_SECRET_KEY": "b", "PATH": "/bin", "HOME": "/home/a",
    }  # fmt: skip
    assert unknown_setting_warnings(env) == []


def test_the_check_never_names_a_prefix_of_its_own() -> None:
    import inspect

    from agent_service import config

    assert "TEAM" not in inspect.getsource(config.unknown_setting_warnings)
