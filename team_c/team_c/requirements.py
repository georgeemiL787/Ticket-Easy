"""Server-derived access/identity requirements for proposals.

They come from the operations a proposal uses, not from model questions. Declared authentication,
a nonempty answer or a model finding never resolves one alone: resolution needs the current
reconciliation to assess the latest answer as sufficient AND an explicit owner confirmation.
"""
import json
from .persistence.util import digest, dump, now

RUNTIME_NOTE = "Approval to build is not activation. Runtime authorization is not implemented or verified by any test."
TEXT = {
    "caller_access": "Who may use {ops}? The API declares authentication but not which users, roles or permissions are allowed.",
    "record_scope": "Which records may a caller reach through {field} in {ops} (for example only records they own)? The API does not declare ownership or data scoping.",
    "public_access": "Should {ops} be usable without authentication? No security requirement is declared, which does not prove public access.",
    "account_administration": "Do {ops} act on other users' accounts? Confirm who may use them; they may be administrative.",
    "data_scope": "Whose records can {ops} return to each caller? The caller does not choose a record, and the API does not declare whether a caller receives only their own data.",
}


READS = {"GET", "HEAD"}


def returns_records(op):
    """Whether the success response carries records: an object with fields, or a list of them (directly or in a field)."""
    success = next((r for c, r in sorted(op.get("responses", {}).items()) if c.startswith("2") and r.get("schema")), None)
    schema = (success or {}).get("schema") or {}
    record = lambda s: bool((s or {}).get("properties")) or (s or {}).get("type") == "object" and bool((s or {}).get("additionalProperties"))
    listing = lambda s: (s or {}).get("type") == "array" and record(s.get("items"))
    return record(schema) or listing(schema) or any(listing(p) for p in (schema.get("properties") or {}).values())


def selects_records(op, carried):
    """Whether the caller picks which record(s) the operation acts on: a path id, a required query filter, or a carried reference."""
    return any(k.startswith("path.") or k in carried or (k.startswith("query.") and spec.get("required")) for k, spec in op["inputs"].items())


def derive(steps, inventory):
    ops = {o["id"]: o for o in inventory["operations"]}
    groups = {}
    seen = set()
    for step in steps:
        op = ops.get(step["operation_id"])
        if not op or "authorization" not in op:
            continue
        label = f'{op["method"]} {op["path"]}'
        first = op["id"] not in seen
        seen.add(op["id"])
        if first:
            auth = op["declared_auth"]
            schemes = sorted({f'{a["scheme"]} ({a["type"]})' for option in auth["alternatives"] for a in option})
            kind = "caller_access" if auth["status"] in ("required", "optional") else "public_access"
            groups.setdefault((kind, ""), []).append((label, f'{label}: declared authentication {auth["status"]}' + (" via " + ", ".join(schemes) if schemes else "")))
        carried = {b["target"]: b["step_id"] for b in step.get("bindings", []) if b["kind"] == "previous_operation_output"}
        for key, spec in op["inputs"].items():
            # Record selectors: path parameters, required query parameters, and identifiers carried from an earlier response.
            if key.startswith("path."):
                fact = f"{label}: {key} selects the record"
            elif key.startswith("query.") and spec.get("required"):
                fact = f"{label}: required {key} selects records"
            elif key in carried:
                fact = f"{label}: {key} carries a record reference from {carried[key]}'s response"
            else:
                continue
            group = groups.setdefault(("record_scope", key), [])
            if label not in [existing for existing, _ in group]:
                group.append((label, fact + "; ownership/data scoping is not declared"))
        # A read behind a login that returns records without the caller selecting one still decides whose data
        # the caller receives (a listing, a profile, a report). Selected records are covered by record_scope above.
        if first and op["method"] in READS and op["declared_auth"]["status"] in ("required", "optional") \
                and not selects_records(op, carried) and returns_records(op):
            groups.setdefault(("data_scope", label), []).append((label, f"{label}: returns records the caller does not select; whose data they are is not declared"))
        if first and any(s["kind"] == "account_records" for s in op["exposure"]["signals"]):
            groups.setdefault(("account_administration", ""), []).append((label, f"{label}: touches user/account records (name heuristic)"))
    result = []
    for (kind, field), items in groups.items():
        labels = [label for label, _ in items]
        key = digest(dict(kind=kind, field=field, operations=sorted(labels)))
        result.append(dict(id=f"{kind}-{key[:6]}", kind=kind, field=(field or None) if kind != "data_scope" else None, text=TEXT[kind].format(ops=", ".join(labels), field=field),
                           operations=labels, source_facts=[fact for _, fact in items] + ["Authorization rules are not declared by the source."]))
    return result


def for_version(conn, pid, version):
    row = conn.execute("SELECT v.content,s.inventory FROM versions v JOIN specifications s ON s.id=v.spec_id WHERE v.proposal_id=? AND v.version=?", (pid, version)).fetchone()
    return derive(json.loads(row["content"])["steps"], json.loads(row["inventory"]))


def ensure(conn, pid, version):
    """Persist this version's requirements (idempotent: content and inventory are immutable)."""
    reqs = for_version(conn, pid, version)
    for r in reqs:
        conn.execute("INSERT OR IGNORE INTO requirements VALUES(?,?,?,?,?,?,?,?)", (r["id"], pid, r["kind"], r["text"], dump(r["operations"]), dump(r["source_facts"]), version, now()))
        conn.execute("INSERT OR IGNORE INTO requirement_scope VALUES(?,?,?)", (pid, version, r["id"]))
    return reqs


def event(conn, pid, version, rid, status, actor, note, basis=None, answer_id=None, reconciliation_id=None):
    conn.execute("INSERT INTO requirement_events(proposal_id,version,requirement_id,status,basis,answer_id,reconciliation_id,note,actor,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                 (pid, version, rid, status, basis, answer_id, reconciliation_id, note, actor, now()))


def current(conn, pid, version, reqs, answers, reconciliation_id):
    """Effective status: an assessment or confirmation only counts for the latest answer."""
    result = []
    for r in reqs:
        answer = answers.get(r["id"])
        events = [dict(e) for e in conn.execute("SELECT * FROM requirement_events WHERE proposal_id=? AND version=? AND requirement_id=? ORDER BY id", (pid, version, r["id"]))]
        last = events[-1] if events else None
        if not answer or not answer["text"].strip():
            status = "unanswered"
        elif not last or last["answer_id"] != answer["id"] or (last["status"] != "owner_confirmed" and last["reconciliation_id"] != reconciliation_id):
            status = "awaiting_reconciliation"
        else:
            status = last["status"]
        confirmed = status == "owner_confirmed"
        result.append(dict(r, status=status, answer=answer, events=events,
                           evidence=dict(source_documented=r["source_facts"], owner_confirmed=answer["text"] if confirmed else None,
                                         test_verified=None, runtime="awaiting_implementation")))
    return result
