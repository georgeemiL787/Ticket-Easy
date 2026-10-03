"""Repair-only live check, reusing the saved second-domain state instead of regenerating it.

Copies data/desk-base.sqlite3 (opened read-only) to an isolated test database. In the copy, the saved proposal is
cloned under a new id "as of" a version: every row (versions, answers, reconciliations, TEST-ONLY decisions,
requirement events) is copied unchanged apart from ids, so no repair history from the source applies. Only disposable
fixture data is recreated. Cases:
- mapping: the FAULT-INJECTED version (authored defect: the holder id wired into the booking id) gets up to two live
  repair attempts; a repaired version goes through TEST-ONLY review, rebuild, the original test and the cross-user check.
- capability: the correct approved version is asked for an attachment id the API never returns.
Live model calls: repair attempts, plus a reconciliation only if a repair produced a new version.
"""
import argparse
import hashlib
import json
import secrets
import sqlite3
import time
from pathlib import Path
from domain_demo import LABEL, PREFLIGHT_REFUSALS, Backend, build, mapping_check, review, run_case, start_fixture
from fixtures.service_desk.app import VARIANTS
from team_c.config import AppError, Settings
from team_c.models import RepairSubmission, SandboxRunSubmission
from team_c.providers import Providers
from team_c.service import Service
from team_c.storage import Store, uid

SOURCE, DB, CONNECTORS = Path("data/desk-base.sqlite3"), "data/desk-repair.sqlite3", "data/desk-repair-connectors.json"


def clone(db, pid, upto):
    """TEST-DB CLONE of proposal pid as of version upto; decisions keep their TEST-ONLY reasons and snapshots."""
    new = uid()
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    rows = lambda sql, *a: [dict(r) for r in c.execute(sql, a)]
    rec, ans = {}, {}
    with c:
        business = c.execute("SELECT business_id FROM proposals WHERE id=?", (pid,)).fetchone()[0]
        c.execute("INSERT INTO proposals VALUES(?,?,?)", (new, business, upto))
        for r in rows("SELECT * FROM reconciliations WHERE proposal_id=? AND version<=?", pid, upto):
            rec[r["id"]] = uid()
        decisions = {r["version"]: r for r in rows("SELECT * FROM decisions WHERE proposal_id=? AND version<=?", pid, upto)}
        for v in rows("SELECT * FROM versions WHERE proposal_id=? AND version<=? ORDER BY version", pid, upto):
            if v["version"] == upto and decisions.get(upto, {}).get("action") == "approve_to_build":
                v["state"] = "approved_to_build"
            v.update(proposal_id=new, reconciliation_id=rec.get(v["reconciliation_id"]))
            c.execute(f"INSERT INTO versions({','.join(v)}) VALUES({','.join('?' * len(v))})", list(v.values()))
        for r in rows("SELECT * FROM reconciliations WHERE proposal_id=? AND version<=?", pid, upto):
            c.execute("INSERT INTO reconciliations VALUES(?,?,?,?,?,?,?,?)", (rec[r["id"]], new, r["version"], r["snapshot_hash"], r["result"], r["successful"], r["run_id"], r["created_at"]))
        for a in rows("SELECT * FROM answers WHERE proposal_id=? AND version<=? ORDER BY id", pid, upto):
            ans[a["id"]] = c.execute("INSERT INTO answers(proposal_id,version,question_id,text,reviewer,created_at) VALUES(?,?,?,?,?,?)",
                                     (new, a["version"], a["question_id"], a["text"], a["reviewer"], a["created_at"])).lastrowid
        for d in decisions.values():
            c.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?,?)", (uid(), new, d["version"], d["action"], d["reason"], d["reviewer"], d["idempotency_key"] + ":clone:" + new[:8], d["request_hash"], d["snapshot"], d["created_at"]))
        for r in rows("SELECT * FROM requirements WHERE proposal_id=? AND created_version<=?", pid, upto):
            c.execute("INSERT INTO requirements VALUES(?,?,?,?,?,?,?,?)", (r["id"], new, r["kind"], r["text"], r["operations"], r["source_facts"], r["created_version"], r["created_at"]))
        for r in rows("SELECT * FROM requirement_scope WHERE proposal_id=? AND version<=?", pid, upto):
            c.execute("INSERT INTO requirement_scope VALUES(?,?,?)", (new, r["version"], r["requirement_id"]))
        for e in rows("SELECT * FROM requirement_events WHERE proposal_id=? AND version<=? ORDER BY id", pid, upto):
            c.execute("INSERT INTO requirement_events(proposal_id,version,requirement_id,status,basis,answer_id,reconciliation_id,note,actor,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (new, e["version"], e["requirement_id"], e["status"], e["basis"], ans.get(e["answer_id"]), rec.get(e["reconciliation_id"]), e["note"], e["actor"], e["created_at"]))
        for s in rows("SELECT * FROM question_supersessions WHERE proposal_id=? AND new_version<=?", pid, upto):
            c.execute("INSERT INTO question_supersessions VALUES(?,?,?,?,?,?,?,?,?)", (uid(), new, s["version"], s["question_id"], s["question"], s["reason"], s["reviewer"], s["new_version"], s["created_at"]))
    c.close()
    return new


def repair_runs(service):
    return service.store.one("SELECT COUNT(*) AS n FROM runs WHERE kind='repair'")["n"]


def attempt_log(service, pid, outcome, started):
    req = service.store.all("SELECT instruction FROM revision_requests WHERE proposal_id=? AND reviewer='automated_repair' ORDER BY created_at", (pid,))[-1]["instruction"]
    parts = json.loads(req[req.index("{"):])
    return dict(seconds=round(time.time() - started), outcome=outcome, instruction_chars=len(req), contract_evidence=parts["contract_evidence"], earlier_attempts=parts.get("earlier_attempts"))


def repair_loop(service, pid, test_id, verification, log, label):
    """At most the service's two model attempts; a refused attempt is followed by the next one with its feedback."""
    for _ in range(2):
        started = time.time()
        try:
            outcome = service.repair(test_id, RepairSubmission(verification=verification))
        except AppError as exc:
            if exc.code in PREFLIGHT_REFUSALS:
                log.append(dict(step=f"{label}:refused_before_model", code=exc.code, message=exc.message))
                return None
            record = service.store.one("SELECT detail FROM repairs WHERE id=?", (exc.details["repair_id"],))
            log.append(dict(step=f"{label}:attempt_not_accepted", code=exc.code, feedback=record["detail"], **attempt_log(service, pid, None, started)))
            continue
        log.append(dict(step=f"{label}:attempt", **attempt_log(service, pid, outcome, started)))
        return outcome
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8004)
    parser.add_argument("--case", choices=["mapping", "capability", "both"], default="both")
    args = parser.parse_args()
    n = VARIANTS["base"]
    real = Path("data/openapi-live.sqlite3")
    real_hash = hashlib.sha256(real.read_bytes()).hexdigest() if real.exists() else None
    for suffix in ("", "-wal", "-shm"):
        Path(DB + suffix).unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{SOURCE}?mode=ro", uri=True)
    dst = sqlite3.connect(DB)
    src.backup(dst)
    src.close(), dst.close()
    source_pid, source_versions = (lambda c: (c.execute("SELECT id FROM proposals").fetchone()[0], c.execute("SELECT version,state FROM versions ORDER BY version").fetchall()))(sqlite3.connect(DB))
    key, base = secrets.token_urlsafe(24), f"http://127.0.0.1:{args.port}"
    proc = start_fixture("base", args.port, key)
    log = [dict(step="setup", source_db=str(SOURCE), isolated_db=DB, source_proposal=source_pid, source_versions=source_versions,
                note="No generation or initial review is repeated; saved TEST-ONLY review state is cloned in the isolated copy.")]
    try:
        backend = Backend(base, key)
        people = {name: backend.account(name) for name in ("alice", "bob")}
        booking = {name: backend.booking(p["id"]) for name, p in people.items()}
        ctx = n["holder_id"]
        settings = Settings(database_path=DB, connectors_file=CONNECTORS, sandbox_hosts=f"127.0.0.1:{args.port}",
                            ollama_context=40960, ollama_timeout=900, llm_primary="ollama", llm_fallback="none")
        store = Store(DB)
        service = Service(settings, store, Providers(settings, store))
        business = store.one("SELECT business_id FROM proposals WHERE id=?", (source_pid,))["business_id"]
        Path(CONNECTORS).write_text(json.dumps(dict(connectors={"desk-sandbox": dict(business_id=business, base_url=base, sandbox=True, context_fields=[ctx], identities={
            name: dict(token=p["token"], scope="end_user", context={ctx: p["id"]}) for name, p in people.items()})})), encoding="utf-8")
        ref, cat, text = f'query.{n["ref"]}', f'body.{n["category"]}', f'body.{n["text"]}'
        values = {ref: booking["alice"]["ref"], cat: n["categories"][0], text: "Kitchen light flickers"}
        own = lambda new: len(new) == 1 and new[0]["booking"] == booking["alice"]["id"] and new[0]["filed_by"] == people["alice"]["id"]
        none = lambda new: new == []
        if args.case in ("mapping", "both"):
            pid = clone(DB, source_pid, 3)
            view = service.view(pid)
            log.append(dict(step="mapping:clone", proposal_id=pid, version=view["version"], state=view["state"], defective_binding=mapping_check(service, view, n)["carried_binding"],
                            provenance=store.all("SELECT provider,model FROM attempts WHERE run_id=?", (view["run_id"],))))
            a = build(service, pid, n, ctx, log, "mapping:build_defective")
            if a:
                result, new = run_case(service, backend, a, "owner_files_request", "alice", values, dict(status="succeeded"), own, log)
                original = store.one("SELECT expectation FROM sandbox_tests WHERE id=?", (result["test_id"],))["expectation"]
                verification = f"TEST harness: fixture state read directly shows {len(new)} new service requests after the failed run." if not new else None
                before = repair_runs(service)
                outcome = repair_loop(service, pid, result["test_id"], verification, log, "mapping:repair")
                if outcome and outcome["outcome"] == "revised":
                    view = service.view(pid)
                    log.append(dict(step="mapping:revised_version", version=view["version"], state=view["state"], enforcement=view["enforcement"],
                                    requirement_states=[r["status"] for r in view["requirements"]], binding_after=mapping_check(service, view, n)["carried_binding"],
                                    carried_as_expected=mapping_check(service, view, n)["carried_as_expected"], change_summary=view["change_summary"]))
                    if review(service, pid, log, "mapping:review_repaired") and (a2 := build(service, pid, n, ctx, log, "mapping:build_repaired")):
                        run_case(service, backend, a2, "owner_files_request", "alice", values, json.loads(original), own, log)
                        run_case(service, backend, a2, "cross_user_request", "bob", values, dict(status="failed", failure_step=a2["content"]["steps"][0]["id"], failure_outcome="blocked_by_access_check"), none, log)
                    try:
                        service.run_sandbox(a["id"], SandboxRunSubmission(identity="alice", arguments={}))
                    except AppError as exc:
                        log.append(dict(step="mapping:defective_artifact_after_repair", code=exc.code))
                elif repair_runs(service) - before == 2:
                    calls = repair_runs(service)
                    try:
                        service.repair(result["test_id"], RepairSubmission(verification=verification))
                    except AppError as exc:
                        log.append(dict(step="mapping:third_call", code=exc.code, message=exc.message, model_calls_during_check=repair_runs(service) - calls))
                log.append(dict(step="mapping:history", repairs=[(r["attempt"], r["from_version"], r["outcome"], r["new_version"]) for r in service.view(pid)["repairs"]],
                                versions=[(v["version"], v["state"]) for v in service.view(pid)["versions"]], repair_model_calls=repair_runs(service) - before))
        if args.case in ("capability", "both"):
            pid = clone(DB, source_pid, 2)
            a = build(service, pid, n, ctx, log, "capability:build")
            if a:
                result, _ = run_case(service, backend, a, "attachment_reference", "alice", values, dict(status="succeeded", outputs_present=["attachment_id"]), own, log)
                outcome = repair_loop(service, pid, result["test_id"], None, log, "capability:repair")
                log.append(dict(step="capability:history", final=outcome and outcome["outcome"], repairs=[(r["attempt"], r["outcome"], r["detail"][:300]) for r in service.view(pid)["repairs"]],
                                version_after=service.view(pid)["version"]))
    finally:
        proc.terminate()
        log.append(dict(step="real_database_unchanged", sha256_same=real_hash is None or hashlib.sha256(real.read_bytes()).hexdigest() == real_hash))
        print(json.dumps(log, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
