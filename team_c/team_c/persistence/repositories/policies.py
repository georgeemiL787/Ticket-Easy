"""Append-only accepted policy retrieval."""
from ...config import AppError
from ..util import digest
def current(store, pid, version):
    rows = store.all("SELECT * FROM capability_policies WHERE proposal_id=? AND version=? ORDER BY sequence DESC LIMIT 1", (pid, version))
    if not rows:
        return None
    import json
    row = rows[0]
    row["content"] = json.loads(row["content"])
    if digest(row["content"]) != row["sha256"]:
        raise AppError("policy_integrity", "Stored policy content does not match its accepted hash", 409)
    return row


