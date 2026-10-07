"""Live progress and cancellation of the running browser action, shared by model providers and background jobs.

A background job sets LIVE.job on its worker thread; providers append steps to it and check for cancellation.
Thinking text is shown while a step runs and is never stored.
"""
import threading
import time
from .config import AppError

LIVE = threading.local()


def live_step(kind, provider, thinking, retry=False, fixing=False):
    job = getattr(LIVE, "job", None)
    if job is None:
        return None
    step = dict(kind=kind, provider=provider, thinking_on=thinking, retry=retry, fixing=fixing, thinking="", answer_tokens=0, started=time.time(), finished=None)
    job["steps"].append(step)
    return step


def check_cancelled():
    job = getattr(LIVE, "job", None)
    if job is not None and job.get("cancelled"):
        raise AppError("cancelled", "Stopped by the owner", 409)
