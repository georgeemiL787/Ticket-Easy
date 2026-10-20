"""Startup recovery with concurrent processes (REAL separate OS processes sharing one SQLite database)."""
import json
import os
import sqlite3
import subprocess
import sys
from conftest import ROOT
from team_c import storage
from team_c.storage import Store, now

CHILD = r"""
import sqlite3, sys
from team_c.storage import OWNER, Store, now
store = Store(sys.argv[1])
db = sqlite3.connect(sys.argv[1])
db.execute("INSERT INTO runs(id,business_id,kind,input_hash,status,created_at,owner) VALUES('child-run','b','generation','h','running',?,?)", (now(), OWNER))
db.execute("INSERT INTO executions(id,artifact_id,mode,identity,status,created_at,owner) VALUES('child-exec','a','sandbox','alice','running',?,?)", (now(), OWNER))
db.commit()
print("ready", flush=True)
sys.stdin.readline()
"""


def rows(db):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    return {r["id"]: dict(r) for r in c.execute("SELECT id,status,report FROM executions UNION ALL SELECT id,status,NULL FROM runs")}


def test_starting_a_process_leaves_another_live_process_running(tmp_path):
    db = str(tmp_path / "shared.sqlite3")
    Store(db)
    child = subprocess.Popen([sys.executable, "-c", CHILD, db], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, cwd=str(ROOT), env=dict(os.environ, PYTHONPATH=str(ROOT)))
    try:
        assert child.stdout.readline().strip() == "ready"
        c = sqlite3.connect(db)
        c.execute("INSERT INTO executions(id,artifact_id,mode,identity,status,created_at,owner) VALUES('gone','a','sandbox','bob','running',?,?)", (now(), "0" * 32))
        c.execute("INSERT INTO executions(id,artifact_id,mode,identity,status,created_at) VALUES('legacy','a','sandbox','bob','running',?)", (now(),))
        c.execute("INSERT INTO executions(id,artifact_id,mode,identity,status,created_at,owner) VALUES('mine','a','sandbox','bob','running',?,?)", (now(), storage.OWNER))
        c.commit()
        Store(db)  # a second process start while the child is live
        state = rows(db)
        assert state["child-run"]["status"] == "running" and state["child-exec"]["status"] == "running"
        assert state["mine"]["status"] == "running"  # this process is alive too
        assert state["gone"]["status"] == "interrupted" and state["legacy"]["status"] == "interrupted"
        assert json.loads(state["gone"]["report"])["message"].startswith("The owning process ended")
    finally:
        child.kill()
        child.wait()
    # The child crashed without cleanup: the OS released its lock, so the next start interrupts its work.
    Store(db)
    state = rows(db)
    assert state["child-run"]["status"] == "interrupted" and state["child-exec"]["status"] == "interrupted"
    assert "may or may not have been applied" in json.loads(state["child-exec"]["report"])["message"]
    assert state["mine"]["status"] == "running"
    assert not any(p.stem != storage.OWNER for p in (tmp_path / "shared.sqlite3.owners").glob("*.lock"))


def test_a_finished_execution_is_never_rewritten(tmp_path):
    db = str(tmp_path / "done.sqlite3")
    Store(db)
    c = sqlite3.connect(db)
    c.execute("INSERT INTO executions(id,artifact_id,mode,identity,status,report,created_at,owner) VALUES('done','a','sandbox','bob','succeeded','{}',?,?)", (now(), "0" * 32))
    c.commit()
    Store(db)
    assert rows(db)["done"]["status"] == "succeeded"
