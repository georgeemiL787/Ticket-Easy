import os
import uuid
from pathlib import Path

from .util import dump, now

# Execution ownership: each process holds an exclusive OS lock on <db>.owners/<OWNER>.lock for its whole
# lifetime. The OS releases it when the process exits, including crashes, so a lock that can be acquired
# proves its owner is gone. A live owner's lock can never be acquired, however slow or idle it is.
OWNER = uuid.uuid4().hex
_held = {}
INTERRUPTED = {"status": "interrupted", "message": "The owning process ended before this execution finished. A write may or may not have been applied; check the target before retrying. Nothing was retried."}


def _lock(f, unlock=False):
    if os.name == "nt":
        import msvcrt
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB)


def _owners(path):
    return Path(str(path) + ".owners")


def owner_alive(path, owner):
    """False only when the owner's lock file is gone or its lock can be taken (the owner has exited)."""
    if owner == OWNER:
        return True
    lock = _owners(path) / f"{owner}.lock"
    try:
        f = open(lock, "r+b")
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        _lock(f)
    except OSError:
        f.close()
        return True
    _lock(f, unlock=True)
    f.close()
    lock.unlink(missing_ok=True)
    return False


def claim(path):
    """Hold this process's ownership lock for the database; also removes lock files of exited processes."""
    key = str(Path(path).resolve())
    if key in _held:
        return
    folder = _owners(path)
    folder.mkdir(exist_ok=True)
    f = open(folder / f"{OWNER}.lock", "a+b")
    _lock(f)
    _held[key] = f
    for other in folder.glob("*.lock"):
        if other.stem != OWNER:
            owner_alive(path, other.stem)


def recover(conn, path):
    """Interrupt only runs/executions whose owning process has demonstrably exited.

    Rows without an owner predate ownership tracking and keep the previous behavior. A write may have
    been sent before the interruption; its outcome is unknown, never "nothing changed".
    """
    for table in ("runs", "executions"):
        rows = conn.execute(f"SELECT id,owner FROM {table} WHERE status='running'").fetchall()
        dead = {o for o in {r["owner"] for r in rows} if o is None or not owner_alive(path, o)}
        for r in rows:
            if r["owner"] in dead:
                if table == "runs":
                    conn.execute("UPDATE runs SET status='interrupted', completed_at=? WHERE id=? AND status='running'", (now(), r["id"]))
                else:
                    conn.execute("UPDATE executions SET status='interrupted', completed_at=?, report=COALESCE(report, ?) WHERE id=? AND status='running'", (now(), dump(INTERRUPTED), r["id"]))
