from ..util import now
from .versions import BASELINE, MIGRATIONS

TABLE = "CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"


def applied(conn):
    """Recorded migrations in version order; empty before any has run."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone():
        return []
    return [dict(version=r[0], name=r[1], applied_at=r[2]) for r in conn.execute("SELECT version,name,applied_at FROM schema_migrations ORDER BY version")]


def migrate(conn, migrations=MIGRATIONS):
    """Apply pending migrations in order; each runs and is recorded in one transaction, or not at all.

    conn must be in autocommit mode (isolation_level=None) so each BEGIN IMMEDIATE spans exactly one migration.
    """
    done = {m["version"] for m in applied(conn)}
    for version, name, apply in migrations:
        if version in done:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(TABLE)
            if not conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (version,)).fetchone():
                apply(conn)
                conn.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES(?,?,?)", (version, name, now()))
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
