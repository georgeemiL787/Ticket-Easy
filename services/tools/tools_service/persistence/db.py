import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..config import AppError
from .migrations import applied, migrate
from .recovery import OWNER, claim, recover
from .util import digest, dump, now, uid


def _open(path, **kwargs):
    conn = sqlite3.connect(path, timeout=15, **kwargs)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        claim(path)
        conn = _open(self.path, isolation_level=None)
        try:
            migrate(conn)
        finally:
            conn.close()
        self.recover()

    def recover(self):
        with self.connect(write=True) as conn:
            recover(conn, self.path)

    def migrations(self):
        with self.connect() as conn:
            return applied(conn)

    @contextmanager
    def connect(self, write=False):
        conn = _open(self.path)
        if write:
            conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def one(self, sql, args=()):
        with self.connect() as conn:
            row = conn.execute(sql, args).fetchone()
        if row is None:
            raise AppError("not_found", "Record not found", 404)
        return dict(row)

    def all(self, sql, args=()):
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    def start_run(self, business_id, kind, payload):
        run_id = uid()
        with self.connect(write=True) as conn:
            conn.execute("INSERT INTO runs(id,business_id,kind,input_hash,status,created_at,owner) VALUES(?,?,?,?,?,?,?)", (run_id,business_id,kind,digest(payload),"running",now(),OWNER))
        return run_id

    def finish_run(self, run_id, result=None, error=None):
        with self.connect(write=True) as conn:
            conn.execute("UPDATE runs SET status=?,result=?,error=?,completed_at=? WHERE id=?", ("failed" if error else "succeeded",dump(result) if result is not None else None,dump(error) if error else None,now(),run_id))

    def attempt(self, run_id, provider, model, status, error=None):
        with self.connect(write=True) as conn:
            conn.execute("INSERT INTO attempts(run_id,provider,model,status,error,created_at) VALUES(?,?,?,?,?,?)", (run_id,provider,model,status,dump(error) if error else None,now()))

    def diagnostic(self, run_id, stage, payload):
        with self.connect(write=True) as conn:
            conn.execute("INSERT INTO diagnostics(run_id,stage,payload,created_at) VALUES(?,?,?,?)", (run_id,stage,dump(payload),now()))
