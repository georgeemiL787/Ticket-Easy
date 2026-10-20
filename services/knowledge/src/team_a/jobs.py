"""Background jobs of the admin API (re-index, rule extraction): started on a thread, polled by id. In memory on purpose:
a job lost with a restart is simply asked for again."""

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()
KEEP = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def running(kind: str, tenant_id: str) -> dict[str, Any] | None:
    with _lock:
        return next((j for j in _jobs.values() if j["kind"] == kind and j["tenant_id"] == tenant_id and j["status"] == "running"), None)


def start(kind: str, tenant_id: str, work: Callable[[], Any]) -> dict[str, Any]:
    job = {"job_id": uuid.uuid4().hex, "kind": kind, "tenant_id": tenant_id, "status": "running", "started_at": _now(),
           "finished_at": None, "result": None, "error": None}
    with _lock:
        _jobs[job["job_id"]] = job
        for old in list(_jobs)[:-KEEP]:
            _jobs.pop(old, None)

    def run() -> None:
        try:
            job["result"] = work()
            job["status"] = "succeeded"
        except Exception as exc:  # the reason is the job's answer; the thread must not die noisily
            job["error"] = f"{type(exc).__name__}: {exc}"[:500]
            job["status"] = "failed"
        finally:
            job["finished_at"] = _now()

    threading.Thread(target=run, daemon=True).start()
    return dict(job)


def get(job_id: str) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None
