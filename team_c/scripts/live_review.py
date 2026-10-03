"""Live-model review lifecycle check on a COPY of a proposal database.

The source database is opened read-only and copied; every write (supersession, labeled TEST-ONLY
answers, confirmations, the approval) happens in the copy. Nothing here is a business decision.
"""
import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path
import httpx
from team_c.config import AppError, Settings
from team_c.models import AnswerSubmission, DecisionSubmission, ReviewSubmission, SupersedeSubmission
from team_c.providers import Providers
from team_c.service import Service
from team_c.storage import Store

LABEL = "TEST-ONLY (live check on a database copy; not a business decision): "
ANSWERS = {
    "question": "ownership is checked by comparing the item's owner with the authenticated caller before it is returned or updated.",
    "caller_access": "only authenticated users may call these operations, and only for items they own; no administrator access.",
    "record_scope": "a caller may read or update only items whose owner is that caller.",
    "public_access": "these operations must not be used without authentication.",
    "account_administration": "these operations must not act on other users' accounts.",
}


class Recording(httpx.HTTPTransport):
    def __init__(self):
        super().__init__()
        self.bodies = []

    def handle_request(self, request):
        self.bodies.append(json.loads(request.content))
        return super().handle_request(request)


def size(value):
    return len(json.dumps(value, ensure_ascii=False).encode())


def request_sizes(body):
    """HTTP bytes vs prompt text bytes; the schema is a decoding grammar, not prompt text."""
    user = body["messages"][1]["content"]
    data = json.loads(user.split("UNTRUSTED_DATA\n", 1)[1].rsplit("\nEND_UNTRUSTED_DATA", 1)[0])
    ops = data["inventory"]["operations"]
    return dict(
        http_request_bytes=size(body), grammar_schema_bytes=size(body["format"]), num_ctx=body["options"]["num_ctx"], num_predict=body["options"]["num_predict"], think=body["think"],
        system_text_bytes=len(body["messages"][0]["content"].encode()), user_text_bytes=len(user.encode()),
        user_data_bytes={k: size(v) for k, v in data.items()},
        inventory_field_bytes={k: sum(size(o.get(k)) for o in ops) for k in ("responses", "inputs", "authorization_unresolved", "declared_auth", "description", "summary")},
        response_schemas=sum(1 for o in ops for r in o["responses"].values() if r.get("schema")),
        distinct_response_schemas=len({json.dumps(r["schema"], sort_keys=True) for o in ops for r in o["responses"].values() if r.get("schema")}))


def run_diagnostics(store, run_id):
    rows = store.all("SELECT stage,payload FROM diagnostics WHERE run_id=? ORDER BY id", (run_id,))
    result = {}
    for row in rows:
        payload = json.loads(row["payload"])
        if row["stage"] == "context_budget":
            result["context_budget"] = payload
        elif row["stage"] == "model_response":
            result.update(usage=payload.get("usage"), response_bytes=payload.get("bytes"), response_invalid_json=payload.get("invalid_json", False))
    result["attempts"] = [{k: a[k] for k in ("provider", "model", "status", "error")} for a in store.all("SELECT * FROM attempts WHERE run_id=? ORDER BY id", (run_id,))]
    return result


def reconcile(service, pid, log):
    view = service.view(pid)
    started = time.time()
    try:
        result = service.reconcile(pid, view["version"], view["review_revision"])
    except AppError as exc:
        result = dict(error=exc.code, message=exc.message, details={k: v for k, v in exc.details.items() if k in ("run_id", "errors")})
    after = service.view(pid, view["version"])
    rec = after["reconciliations"][-1]["result"] if after["reconciliations"] else None
    run_id = result.get("run_id") or result.get("details", {}).get("run_id")
    log.append(dict(step="live_reconcile", version=view["version"], seconds=round(time.time() - started, 1), result=result,
                    findings=[{k: f[k] for k in ("question_id", "status", "explanation")} for f in rec["findings"]] if rec else None,
                    revised=bool(rec and rec.get("revised_proposal")), validation_blockers=rec.get("validation_blockers") if rec else None,
                    run=run_diagnostics(service.store, run_id) if run_id else None))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="data/openapi-live.sqlite3")
    parser.add_argument("--copy", default="data/openapi-live-review-copy.sqlite3")
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--no-model", action="store_true", help="capture the reconciliation request without calling the model")
    args = parser.parse_args()
    source_hash = hashlib.sha256(Path(args.source).read_bytes()).hexdigest()
    for suffix in ("", "-wal", "-shm"):
        Path(args.copy + suffix).unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{Path(args.source).resolve().as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(args.copy)
    src.backup(dst)
    dst.close()
    src.close()
    settings = Settings(database_path=args.copy)
    store = Store(args.copy)
    transport = Recording()
    if args.no_model:
        transport.handle_request = lambda request: (transport.bodies.append(json.loads(request.content)), httpx.Response(500))[1]
    service = Service(settings, store, Providers(settings, store, transport))
    pid, log = args.proposal, []
    view = service.view(pid)
    log.append(dict(step="initial", version=view["version"], state=view["state"], questions=view["content"]["questions"],
                    question_review=view["question_review"], requirements=[{k: r[k] for k in ("id", "kind", "text", "status")} for r in view["requirements"]]))
    for qid in sorted({n["question_id"] for n in view["question_review"]}):
        note = next(n["note"] for n in view["question_review"] if n["question_id"] == qid)
        result = service.supersede_question(pid, view["version"], qid, SupersedeSubmission(expected_revision=view["review_revision"], reason=LABEL + note))
        view = service.view(pid)
        log.append(dict(step="supersede", question_id=qid, result=result, change_summary=view["change_summary"]))
    answers = {q["id"]: LABEL + ANSWERS["question"] for q in view["content"]["questions"]} | {r["id"]: LABEL + ANSWERS[r["kind"]] for r in view["requirements"]}
    service.answers(pid, view["version"], AnswerSubmission(expected_revision=view["review_revision"], answers=answers))
    log.append(dict(step="answers", answers=answers))
    for _ in range(3):
        result = reconcile(service, pid, log)
        if "error" in result or not result.get("material_change"):
            break
        log[-1]["change_summary"] = service.view(pid)["change_summary"]
    view = service.view(pid)
    log.append(dict(step="after_reconciliation", version=view["version"], state=view["state"], requirements={r["id"]: r["status"] for r in view["requirements"]}))
    decision = lambda key: DecisionSubmission(expected_revision=service.view(pid)["review_revision"], action="approve_to_build", reason=LABEL + "approval on the copy", idempotency_key=key)
    if view["state"] == "ready_for_review":
        try:
            service.decide(pid, view["version"], decision("live-copy-before-confirm"))
            log.append(dict(step="approve_before_confirm", result="UNEXPECTEDLY APPROVED"))
        except AppError as exc:
            log.append(dict(step="approve_before_confirm", result=exc.code, message=exc.message))
        for r in view["requirements"]:
            if r["status"] == "answer_sufficient":
                service.confirm_requirement(pid, view["version"], r["id"], ReviewSubmission(expected_revision=service.view(pid)["review_revision"]))
        view = service.view(pid)
        log.append(dict(step="confirmed_on_copy", requirements={r["id"]: r["status"] for r in view["requirements"]}))
        try:
            d = service.decide(pid, view["version"], decision("live-copy-approval"))
            log.append(dict(step="approve_on_copy", decision_id=d["id"], lifecycle=service.view(pid)["lifecycle"]))
        except AppError as exc:
            log.append(dict(step="approve_on_copy", result=exc.code, message=exc.message))
    log.append(dict(step="request_sizes", requests=[request_sizes(b) for b in transport.bodies]))
    Path(args.copy).with_suffix(".messages.json").write_text(json.dumps([b["messages"] for b in transport.bodies], ensure_ascii=False), encoding="utf-8")
    with sqlite3.connect(f"file:{Path(args.source).resolve().as_posix()}?mode=ro", uri=True) as src:
        original = src.execute("SELECT current_version,(SELECT COUNT(*) FROM decisions WHERE proposal_id=?),(SELECT COUNT(*) FROM answers WHERE proposal_id=?) FROM proposals WHERE id=?", (pid, pid, pid)).fetchone()
    log.append(dict(step="source_unchanged", sha256_same=hashlib.sha256(Path(args.source).read_bytes()).hexdigest() == source_hash, current_version=original[0], decisions=original[1], answers=original[2]))
    print(json.dumps(log, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
