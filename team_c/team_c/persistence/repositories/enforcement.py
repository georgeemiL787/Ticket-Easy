import json


def get(store, eid):
    row = store.one("SELECT * FROM enforcement_configs WHERE id=?", (eid,))
    row["content"] = json.loads(row["content"])
    latest = store.one("SELECT id FROM enforcement_configs WHERE proposal_id=? AND version=? ORDER BY rowid DESC LIMIT 1", (row["proposal_id"], row["version"]))["id"]
    row["status"] = "superseded" if latest != eid else "approved" if row["reviewed_at"] else "awaiting_review"
    return row


def current(store, pid, version):
    rows = store.all("SELECT id FROM enforcement_configs WHERE proposal_id=? AND version=? ORDER BY rowid DESC LIMIT 1", (pid, version))
    return get(store, rows[0]["id"]) if rows else None
