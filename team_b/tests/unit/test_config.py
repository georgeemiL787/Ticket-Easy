from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from team_b.config import PROJECT_ROOT, Settings, SettingsError


def test_defaults() -> None:
    s = Settings.from_env({})
    assert s.mode == "standin" and s.store == "memory" and s.llm == "none"
    assert s.ollama_url == "http://127.0.0.1:11434" and s.ollama_model == "qwen3:8b"
    assert s.openrouter_api_key is None and s.llm_model is None and s.fixed_today is None
    assert s.llm_timeout_s == 20.0 and s.capability_ttl_s == 60.0 and s.log_json is True
    assert s.config_dir == PROJECT_ROOT / "config" / "tenants"
    assert s.data_dir == PROJECT_ROOT / "data" and s.fixtures_dir == PROJECT_ROOT / "fixtures"
    assert (PROJECT_ROOT / "pyproject.toml").is_file()  # PROJECT_ROOT really is the team_b folder


def test_every_variable_is_read() -> None:
    s = Settings.from_env(
        {
            "TEAM_B_MODE": "live",
            "TEAM_B_CONFIG_DIR": "/tmp/tenants",
            "TEAM_B_DATA_DIR": "/tmp/data",
            "TEAM_B_FIXTURES_DIR": "/tmp/fixtures",
            "TEAM_B_STORE": "sqlite",
            "TEAM_B_DB_PATH": "/tmp/db.sqlite3",
            "TEAM_B_LLM": "openrouter",
            "OLLAMA_URL": "http://ollama:11434",
            "TEAM_B_OLLAMA_MODEL": "llama3.2",
            "OPENROUTER_API_KEY": "sk-or-secret",
            "TEAM_B_LLM_MODEL": "some/model",
            "TEAM_B_LLM_TIMEOUT_S": "7.5",
            "TEAM_B_FIXED_TODAY": "2026-09-28",
            "TEAM_B_CAPABILITY_TTL_S": "5",
            "TEAM_B_LOG_JSON": "false",
        }
    )
    assert s.mode == "live" and s.store == "sqlite" and s.llm == "openrouter"
    assert s.config_dir == Path("/tmp/tenants") and s.data_dir == Path("/tmp/data")
    assert s.fixtures_dir == Path("/tmp/fixtures") and s.db_path == Path("/tmp/db.sqlite3")
    assert s.ollama_url == "http://ollama:11434" and s.ollama_model == "llama3.2"
    assert s.openrouter_api_key is not None and s.openrouter_api_key.get_secret_value() == "sk-or-secret"
    assert s.llm_model == "some/model" and s.llm_timeout_s == 7.5 and s.capability_ttl_s == 5.0
    assert s.fixed_today == date(2026, 9, 28) and s.log_json is False


def test_empty_and_blank_values_count_as_not_set() -> None:
    s = Settings.from_env({"TEAM_B_MODE": "", "TEAM_B_STORE": "   ", "TEAM_B_FIXED_TODAY": ""})
    assert s.mode == "standin" and s.store == "memory" and s.fixed_today is None


def test_values_are_trimmed() -> None:
    assert Settings.from_env({"TEAM_B_MODE": " live "}).mode == "live"


@pytest.mark.parametrize("value", ["true", "True", "1", "yes", "on"])
def test_log_json_true_spellings(value: str) -> None:
    assert Settings.from_env({"TEAM_B_LOG_JSON": value}).log_json is True


@pytest.mark.parametrize("value", ["false", "0", "no", "off"])
def test_log_json_false_spellings(value: str) -> None:
    assert Settings.from_env({"TEAM_B_LOG_JSON": value}).log_json is False


@pytest.mark.parametrize(
    ("env", "variable"),
    [
        ({"TEAM_B_MODE": "production"}, "TEAM_B_MODE"),
        ({"TEAM_B_STORE": "redis"}, "TEAM_B_STORE"),
        ({"TEAM_B_LLM": "gpt"}, "TEAM_B_LLM"),
        ({"TEAM_B_LLM_TIMEOUT_S": "fast"}, "TEAM_B_LLM_TIMEOUT_S"),
        ({"TEAM_B_LLM_TIMEOUT_S": "0"}, "TEAM_B_LLM_TIMEOUT_S"),
        ({"TEAM_B_CAPABILITY_TTL_S": "-1"}, "TEAM_B_CAPABILITY_TTL_S"),
        ({"TEAM_B_FIXED_TODAY": "28/09/2026"}, "TEAM_B_FIXED_TODAY"),
        ({"TEAM_B_LOG_JSON": "maybe"}, "TEAM_B_LOG_JSON"),
    ],
)
def test_bad_values_name_the_variable(env: dict[str, str], variable: str) -> None:
    with pytest.raises(SettingsError, match=variable):
        Settings.from_env(env)


def test_all_problems_are_reported_together() -> None:
    with pytest.raises(SettingsError) as info:
        Settings.from_env({"TEAM_B_MODE": "x", "TEAM_B_STORE": "y"})
    assert "TEAM_B_MODE" in str(info.value) and "TEAM_B_STORE" in str(info.value)


def test_openrouter_needs_a_key() -> None:
    with pytest.raises(SettingsError, match="OPENROUTER_API_KEY"):
        Settings.from_env({"TEAM_B_LLM": "openrouter"})
    assert Settings.from_env({"TEAM_B_LLM": "openrouter", "OPENROUTER_API_KEY": "k"}).llm == "openrouter"


def test_the_api_key_never_shows_in_repr_or_dump() -> None:
    s = Settings.from_env({"OPENROUTER_API_KEY": "sk-or-very-secret"})
    assert "sk-or-very-secret" not in repr(s)
    assert "sk-or-very-secret" not in str(s)
    assert "sk-or-very-secret" not in s.model_dump_json()


def test_settings_are_frozen_and_ignore_the_process_environment_when_given_a_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEAM_B_MODE", "live")
    assert Settings.from_env({}).mode == "standin"  # an explicit mapping wins over os.environ
    assert Settings.from_env().mode == "live"  # no mapping: the real environment is read
    with pytest.raises(ValidationError):
        Settings.from_env({}).mode = "live"  # type: ignore[misc]
