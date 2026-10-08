"""Unified SQLite data layer: structured backend data, the knowledge corpus and read-only mirrors.

Build with `python -m team_a db build`. The database is derived data: the files under data/ stay the
source of truth, and check_action never reads it.
"""

import sqlite3
from pathlib import Path

from team_a.config import settings

SCHEMA_VERSION = "1"
SCHEMA_FILE = Path(__file__).with_name("schema.sql")


class DatabaseNotBuilt(RuntimeError):
    pass


def connect(db_path: Path | None = None, readonly: bool = True) -> sqlite3.Connection:
    """Open the database. Read-only by default: only the loader opens it for writing."""
    path = Path(db_path or settings.db_path)
    if readonly:
        if not path.exists():
            raise DatabaseNotBuilt(f"No database at {path}. Run: python -m team_a db build")
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only = ON")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, isolation_level=None)  # transactions are explicit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
