"""Settings that look like ours under another name are reported, never silently ignored."""
from tools_service.config import Settings, setting_names, unknown_setting_warnings


def test_a_setting_that_ends_like_ours_but_starts_differently_is_reported():
    warnings = unknown_setting_warnings({"ACME_ADMIN_TOKEN": "x", "DATABASE_PATH": "old.sqlite3", "PATH": "/bin"})
    assert "Setting ACME_ADMIN_TOKEN is not used. Did you mean TOOLS_ADMIN_TOKEN?" in warnings
    assert "Setting DATABASE_PATH is not used. Did you mean TOOLS_DATABASE_PATH?" in warnings
    assert len(warnings) == 2


def test_our_own_shared_and_sibling_settings_are_not_reported():
    env = {"TOOLS_ADMIN_TOKEN": "t", "GROQ_API_KEY": "k", "LLM_PRIMARY": "groq", "AGENT_STORE": "memory", "PATH": "/bin"}
    assert unknown_setting_warnings(env) == []


def test_own_settings_use_the_prefix_and_provider_settings_keep_their_names(monkeypatch):
    own, shared = setting_names()
    assert all(name.startswith("TOOLS_") for name in own) and not any(name.startswith("TOOLS_") for name in shared)
    monkeypatch.setenv("TOOLS_DATABASE_PATH", "x.sqlite3")
    monkeypatch.setenv("LLM_PRIMARY", "groq")
    settings = Settings()
    assert settings.database_path == "x.sqlite3" and settings.llm_primary == "groq"
