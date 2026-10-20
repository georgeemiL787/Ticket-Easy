"""Helpers shared by tests: settings that follow AGENT_STORE, so the whole suite can run on either store."""

import os
from pathlib import Path
from typing import Any, Literal, cast

from agent_service.config import Settings

StoreKind = Literal["memory", "sqlite"]


def store_kind() -> StoreKind:
    """memory unless AGENT_STORE=sqlite is set in the environment."""
    return cast(StoreKind, "sqlite" if os.environ.get("AGENT_STORE", "").strip().lower() == "sqlite" else "memory")


def make_settings(db_dir: Path, **overrides: Any) -> Settings:
    """Simulated settings with the store from the environment; a sqlite database lives in db_dir (one per test)."""
    values: dict[str, Any] = {
        "store": store_kind(), "db_path": db_dir / "agent.sqlite3", "llm": "none",
        "auth_required": False,  # most tests are about something else; the sign-in tests switch it on
    }  # fmt: skip
    return Settings(**{**values, **overrides})
