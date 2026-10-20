"""Settings that look like ours under another name are reported, never silently ignored."""

import re
from pathlib import Path

from knowledge_service import config


def test_a_setting_that_ends_like_ours_but_starts_differently_is_reported() -> None:
    warnings = config.unknown_setting_warnings({"ACME_ADMIN_API_KEY": "x", "SERVICE_API_KEY": "old", "PATH": "/bin"})
    assert "Setting ACME_ADMIN_API_KEY is not used. Did you mean KNOWLEDGE_ADMIN_API_KEY?" in warnings
    assert "Setting SERVICE_API_KEY is not used. Did you mean KNOWLEDGE_SERVICE_API_KEY?" in warnings
    assert len(warnings) == 2


def test_our_own_shared_and_sibling_settings_are_not_reported() -> None:
    env = {"KNOWLEDGE_ADMIN_API_KEY": "a", "OLLAMA_URL": "http://x", "OPENROUTER_API_KEY": "k", "AGENT_STORE": "memory",
           "TOOLS_ADMIN_TOKEN": "t", "PATH": "/bin"}  # fmt: skip
    assert config.unknown_setting_warnings(env) == []


def test_the_lists_cover_every_variable_the_config_reads() -> None:
    source = (Path(config.__file__)).read_text(encoding="utf-8")
    read = set(re.findall(r'getenv\("([A-Z0-9_]+)"', source))
    assert read == set(config.OWN_SETTINGS) | set(config.SHARED_SETTINGS), read ^ (set(config.OWN_SETTINGS) | set(config.SHARED_SETTINGS))
