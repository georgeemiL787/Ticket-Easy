"""Helpers shared by tests: settings that follow TEAM_B_STORE, so the whole suite can run on either store."""

import os
from pathlib import Path
from typing import Any, Literal, cast

from team_b.config import Settings

StoreKind = Literal["memory", "sqlite"]


def store_kind() -> StoreKind:
    """memory unless TEAM_B_STORE=sqlite is set in the environment."""
    return cast(StoreKind, "sqlite" if os.environ.get("TEAM_B_STORE", "").strip().lower() == "sqlite" else "memory")


def make_settings(db_dir: Path, **overrides: Any) -> Settings:
    """Stand-in settings with the store from the environment; a sqlite database lives in db_dir (one per test)."""
    values: dict[str, Any] = {"store": store_kind(), "db_path": db_dir / "team_b.sqlite3", "llm": "none"}
    return Settings(**{**values, **overrides})
