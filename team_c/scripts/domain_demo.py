"""Live end-to-end run on the local service-desk fixture, an independent second domain.

OpenAPI discovery -> live-model proposal -> labeled TEST-ONLY review and approval -> operator enforcement
with TEST-ONLY review -> automatic artifact build -> sandbox tests -> independent backend verification.
With --repair: a missing-capability case is sent to live repair, then a labeled FAULT INJECTION (authored,
not model output) creates a mapping defect that the live model must repair (at most twice) through fresh review.
Uses a fresh database; the real review database is only hashed to prove it is unchanged.
"""
import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
import httpx
from fixtures.service_desk.app import VARIANTS
from team_c.config import AppError, Settings
from team_c.models import (AnswerSubmission, ArtifactBuildSubmission, DecisionSubmission, EnforcementReviewSubmission, EnforcementSubmission, GenerationOutput,
                           RepairSubmission, ReviewSubmission, RevisionSubmission, SandboxTestSubmission, SupersedeSubmission)
from team_c.providers import Providers
from team_c.service import Service
from team_c.storage import Store

PREFLIGHT_REFUSALS = {"repair_stale", "repair_not_applicable", "verification_required", "repair_exhausted", "nothing_to_repair"}
LABEL = "TEST-ONLY (live second-domain check; not a business decision): "
ANSWERS = {
    "question": "ownership is verified by comparing the booking holder returned by the lookup with the authenticated customer before any request is filed.",
    "caller_access": "only authenticated customers may use these operations, and only for their own bookings; no staff or administrator use.",
    "record_scope": "a customer may look up and file service requests only for bookings they hold.",
    "public_access": "these operations must not be used without authentication.",
    "account_administration": "these operations must not act on other customers' accounts.",
}


class Backend:
    """Fixture harness client: disposable data and state reads, independent of Team C."""
    def __init__(self, base, key):
        self.http, self.h = httpx.Client(base_url=base, timeout=10, trust_env=False), {"x-harness-key": key}

    def account(self, name):
        return self.http.post("/_harness/accounts", json=dict(name=name), headers=self.h).json()

    def booking(self, account_id):
        return self.http.post("/_harness/bookings", json=dict(account_id=account_id), headers=self.h).json()

    def requests(self):
        return self.http.get("/_harness/state", headers=self.h).json()["requests"]


class FaultInjection:
    """FAULT INJECTION (authored, TEST-ONLY): feeds the holder identifier where the booking identifier belongs."""
    def __init__(self, store, n):
        self.store, self.n = store, n

    def call(self, kind, payload, output_model, run):
        self.store.attempt(run, "FAULT_INJECTION", "authored-test-only", "succeeded")
        content = json.loads(json.dumps(payload["proposal"]))
        for step in content["steps"]:
            for b in step["bindings"]:
                if b["target"] == f'body.{self.n["body_id"]}':
                    b["reference"] = f'/{self.n["lookup_env"]}/{self.n["record"]}/{self.n["holder"]}/{self.n["holder_id"]}'
        return GenerationOutput(proposals=[content], capability_gaps=[])


def start_fixture(variant, port, key):
    env = dict(os.environ, FIXTURE_VARIANT=variant, FIXTURE_HARNESS_KEY=key, PYTHONPATH=".")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "fixtures.service_desk.app:create_app", "--factory", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"], env=env)
    for _ in range(100):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=1, trust_env=False).status_code == 200:
                return proc
        except httpx.HTTPError:
            time.sleep(0.2)
    proc.kill()
    raise RuntimeError("fixture did not start")


def labels(service, view):
    ops = {o["id"]: o for o in service.spec(view["spec_id"])["inventory"]["operations"]}
    return [f'{ops[s["operation_id"]]["method"]} {ops[s["operation_id"]]["path"]}' for s in view["content"]["steps"]]


def mapping_check(service, view, n):
    """Expected wiring, stated from the API contract before looking at the generated proposal."""
    steps = labels(service, view)
    runtime = {b["target"]: b["reference"] for s in view["content"]["steps"] for b in s["bindings"] if b["kind"] == "runtime_argument"}
    carried = next((b for s in view["content"]["steps"] for b in s["bindings"] if b["target"] == f'body.{n["body_id"]}'), None)
    return dict(steps=steps, steps_as_expected=steps == [f'GET {n["prefix"]}{n["lookup"]}', f'POST {n["prefix"]}{n["create"]}'],
                carried_binding={k: carried[k] for k in ("kind", "reference", "step_id", "response_status")} if carried else None,
                carried_as_expected=bool(carried) and carried["kind"] == "previous_operation_output" and carried["reference"] == f'/{n["lookup_env"]}/{n["record"]}/{n["internal"]}',
                runtime_inputs=runtime, outputs=view["content"]["outputs"], configuration=view["content"]["configuration"],
                questions=[q["text"] for q in view["content"]["questions"]], requirements=[(r["kind"], r["field"]) for r in view["requirements"]])


def review(service, pid, log, label):
    """Labeled TEST-ONLY answers, live reconciliation, confirmation of sufficient answers, then approval."""
    view = service.view(pid)
    for qid in sorted({x["question_id"] for x in view["question_review"]}):
        note = next(x["note"] for x in view["question_review"] if x["question_id"] == qid)
        service.supersede_question(pid, view["version"], qid, SupersedeSubmission(expected_revision=view["review_revision"], reason=LABEL + note))
        view = service.view(pid)
        log.append(dict(step=f"{label}:supersede", question_id=qid))
    answers = {q["id"]: LABEL + ANSWERS["question"] for q in view["content"]["questions"]} | {r["id"]: LABEL + ANSWERS[r["kind"]] for r in view["requirements"]}
    service.answers(pid, view["version"], AnswerSubmission(expected_revision=view["review_revision"], answers=answers))
    for _ in range(4):
        view = service.view(pid)
        started = time.time()
        try:
            result = service.reconcile(pid, view["version"], view["review_revision"])
        except AppError as exc:
            log.append(dict(step=f"{label}:reconcile_failed", code=exc.code, message=exc.message))
            return False
        rec = service.view(pid, view["version"])["reconciliations"][-1]["result"]
        log.append(dict(step=f"{label}:live_reconcile", seconds=round(time.time() - started), result=result,
                        findings={f["question_id"]: f["status"] for f in rec["findings"]}, validation_blockers=rec.get("validation_blockers")))
        if result["state"] == "ready_for_review" or not result["material_change"]:
            break
    view = service.view(pid)
    if view["state"] != "ready_for_review":
        log.append(dict(step=f"{label}:not_ready", state=view["state"]))
        return False
    for r in view["requirements"]:
        if r["status"] == "answer_sufficient":
            service.confirm_requirement(pid, view["version"], r["id"], ReviewSubmission(expected_revision=service.view(pid)["review_revision"]))
    view = service.view(pid)
    d = service.decide(pid, view["version"], DecisionSubmission(expected_revision=view["review_revision"], action="approve_to_build", reason=LABEL + "approval",
                                                               idempotency_key=f"{label}-{pid}-{view['version']}"))
    log.append(dict(step=f"{label}:approved_test_only", version=view["version"], decision_id=d["id"]))
    return True


def build(service, pid, n, ctx, log, label):
    """Operator enforcement (authored from the API contract), its TEST-ONLY review, then the automatic build."""
    view = service.view(pid)
    steps = labels(service, view)
    lookup = view["content"]["steps"][steps.index(f'GET {n["prefix"]}{n["lookup"]}')]["id"] if f'GET {n["prefix"]}{n["lookup"]}' in steps else None
    holder = f'/{n["lookup_env"]}/{n["record"]}/{n["holder"]}/{n["holder_id"]}'
    config = {r["id"]: dict(mechanism="delegated_user_credential") if r["kind"] == "caller_access" else
              dict(mechanism="response_field_matches_context", step_id=lookup or "", response_status="200", pointer=holder, context_field=ctx, comparison="equals", check_point="after_step")
              for r in view["requirements"] if r["kind"] in ("caller_access", "record_scope")}
    try:
        e = service.submit_enforcement(pid, EnforcementSubmission(connector_id="desk-sandbox", enforcement=config))
        e = service.review_enforcement(e["id"], EnforcementReviewSubmission(note=LABEL + "enforcement review"))
        a = service.build_artifact(pid, ArtifactBuildSubmission(connector_id="desk-sandbox"))
    except AppError as exc:
        log.append(dict(step=f"{label}:build_failed", code=exc.code, message=exc.message, details={k: v for k, v in exc.details.items() if k == "missing"}))
        return None
    c = a["content"]
    log.append(dict(step=f"{label}:artifact_built", artifact_id=a["id"], sha256=a["sha256"], proposal_version=c["proposal"]["version"], enforcement_config=e["id"],
                    steps=[dict(id=s["id"], call=f'{s["method"]} {s["path"]}', parameters=[(p["location"], p["name"], p["source"]["kind"]) for p in s["parameters"]],
                                body=[(f["name"], f["source"]["kind"], f["source"].get("reference")) for f in (s["body"] or {}).get("fields", [])], responses=list(s["responses"])) for s in c["steps"]],
                    outputs=c["outputs"], enforcement=[{k: r["enforcement"].get(k) for k in ("mechanism", "enforced_by", "step_id", "pointer", "context_field")} for r in c["access_requirements"]],
                    execution_blockers=c["execution_blockers"], activation=c["activation"]))
    return a


def run_case(service, backend, a, name, who, values, expect, verify, log):
    """values are keyed by API input (the contract), mapped to whatever runtime names the proposal chose."""
    names = {b["target"]: b["reference"] for s in json.loads(service.store.one("SELECT content FROM versions WHERE proposal_id=? AND version=?", (a["proposal_id"], a["version"]))["content"])["steps"]
             for b in s["bindings"] if b["kind"] == "runtime_argument"}
    args = {names[k]: v for k, v in values.items() if k in names}
    before = backend.requests()
    try:
        r = service.run_sandbox_test(a["id"], SandboxTestSubmission(name=name, identity=who, arguments=args, expect=expect))
    except AppError as exc:
        r = dict(verdict="error", status=exc.code)
    new = [x for x in backend.requests() if x not in before]
    report = r.get("failure_report") or {}
    log.append(dict(step=f"test:{name}", identity=who, argument_names=sorted(args), unmapped_inputs=sorted(set(values) - set(names)), expect=expect, verdict=r["verdict"],
                    status=r["status"], failure=(report.get("actual") or {}).get("failure"), classification=report.get("classification"), repairable=report.get("repairable"),
                    backend_new_requests=len(new), backend_state_as_expected=verify(new)))
    return r, new


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=sorted(VARIANTS), default="base")
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument("--repair", action="store_true")
    args = parser.parse_args()
    n = VARIANTS[args.variant]
    real = Path("data/openapi-live.sqlite3")
    real_hash = hashlib.sha256(real.read_bytes()).hexdigest() if real.exists() else None
    db, connectors = f"data/desk-{args.variant}.sqlite3", f"data/desk-{args.variant}-connectors.json"
    for suffix in ("", "-wal", "-shm"):
        Path(db + suffix).unlink(missing_ok=True)
    key, base = secrets.token_urlsafe(24), f"http://127.0.0.1:{args.port}"
    proc = start_fixture(args.variant, args.port, key)
    log = []
    try:
        backend = Backend(base, key)
        people = {name: backend.account(name) for name in ("alice", "bob")}
        booking = {name: backend.booking(p["id"]) for name, p in people.items()}
        ctx = n["holder_id"]
        holder = lambda p: p["number"] if n["holder_int"] else p["id"]
        settings = Settings(database_path=db, openapi_fetch_hosts=f"127.0.0.1:{args.port}", connectors_file=connectors, sandbox_hosts=f"127.0.0.1:{args.port}",
                            ollama_context=40960, ollama_timeout=900, llm_primary="ollama", llm_fallback="none")
        store = Store(db)
        live = Providers(settings, store)
        service = Service(settings, store, live)
        business = service.business("Harbor Stay (TEST-ONLY second domain)", Path("examples/service-desk-business.txt").read_text(encoding="utf-8"))
        Path(connectors).write_text(json.dumps(dict(connectors={"desk-sandbox": dict(business_id=business["id"], base_url=base, sandbox=True, context_fields=[ctx], identities={
            name: dict(token=p["token"], scope="end_user", context={ctx: holder(p)}) for name, p in people.items()})})), encoding="utf-8")
        spec = service.fetch(business["id"], base + "/openapi.json")
        inv = spec["inventory"]
        log.append(dict(step="discovery", variant=args.variant, summary=inv["summary"], operations=[dict(call=f'{o["method"]} {o["path"]}', supported=o["supported"], eligible=o.get("proposal_eligible"),
                        blocked=(o.get("binding") or {}).get("errors")) for o in inv["operations"]]))
        started = time.time()
        try:
            generation = service.generate(spec["id"])
        except AppError as exc:
            log.append(dict(step="live_generation_failed", code=exc.code, message=exc.message, run_id=exc.details.get("run_id")))
            return
        log.append(dict(step="live_generation", seconds=round(time.time() - started), run_id=generation["run_id"], proposals=len(generation["proposal_ids"]), capability_gaps=generation["capability_gaps"]))
        views = [service.view(p) for p in generation["proposal_ids"]]
        for v in views:
            log.append(dict(step="generated_proposal", proposal_id=v["proposal_id"], name=v["content"]["name"], state=v["state"], mapping=mapping_check(service, v, n), blockers=v["derived"]["blockers"]))
        chosen = next((v for v in views if mapping_check(service, v, n)["steps_as_expected"]), None) or next((v for v in views if any(x.startswith("POST") for x in labels(service, v))), None)
        if not chosen:
            log.append(dict(step="blocked", reason="No generated proposal files a service request"))
            return
        pid = chosen["proposal_id"]
        if not review(service, pid, log, "review") or not (a := build(service, pid, n, ctx, log, "build")):
            return
        ref, cat, text = f'query.{n["ref"]}', f'body.{n["category"]}', f'body.{n["text"]}'
        own = lambda who: lambda new: len(new) == 1 and new[0]["booking"] == booking[who]["id"] and new[0]["filed_by"] == people[who]["id"]
        none = lambda new: new == []
        lookup_step = a["content"]["steps"][0]["id"]
        run_case(service, backend, a, "owner_files_request", "alice", {ref: booking["alice"]["ref"], cat: n["categories"][1], text: "Shower drain is blocked"}, dict(status="succeeded"), own("alice"), log)
        run_case(service, backend, a, "cross_user_request", "bob", {ref: booking["alice"]["ref"], cat: n["categories"][0], text: "Please clean"},
                 dict(status="failed", failure_step=lookup_step, failure_outcome="blocked_by_access_check"), none, log)
        run_case(service, backend, a, "unknown_reference", "alice", {ref: "BK-00000000", cat: n["categories"][0], text: "x"},
                 dict(status="failed", failure_step=lookup_step, failure_outcome="rejected_by_api"), none, log)
        run_case(service, backend, a, "missing_argument", "alice", {cat: n["categories"][0], text: "x"}, dict(status="rejected"), none, log)
        direct = backend.http.post(n["prefix"] + n["create"], json={n["body_id"]: booking["alice"]["id"], n["category"]: n["categories"][0], n["text"]: "x"},
                                   headers={"Authorization": "Bearer " + people["bob"]["token"]})
        log.append(dict(step="backend_rule_direct_cross_user_post", http_status=direct.status_code, expected=403))
        if args.repair:
            repair_demo(service, backend, live, pid, a, n, ctx, people, booking, log)
        executions = service.store.all("SELECT e.status,COUNT(*) AS n FROM executions e JOIN artifacts a ON a.id=e.artifact_id WHERE a.proposal_id=? GROUP BY e.status", (pid,))
        log.append(dict(step="history_after_restart", executions={r["status"]: r["n"] for r in Service(settings, Store(db), live).store.all(
            "SELECT e.status,COUNT(*) AS n FROM executions e JOIN artifacts a ON a.id=e.artifact_id WHERE a.proposal_id=? GROUP BY e.status", (pid,))}, before_restart={r["status"]: r["n"] for r in executions},
            versions=[(v["version"], v["state"]) for v in service.view(pid)["versions"]], repairs=[(r["attempt"], r["outcome"], r["new_version"]) for r in service.view(pid)["repairs"]]))
    finally:
        proc.terminate()
        log.append(dict(step="real_database_unchanged", sha256_same=real_hash is None or hashlib.sha256(real.read_bytes()).hexdigest() == real_hash))
        text = json.dumps(log, indent=2, ensure_ascii=False)
        print(text)


def repair_demo(service, backend, live, pid, current, n, ctx, people, booking, log):
    ref, cat, text = f'query.{n["ref"]}', f'body.{n["category"]}', f'body.{n["text"]}'
    values = {ref: booking["alice"]["ref"], cat: n["categories"][0], text: "Kitchen light flickers"}
    own = lambda new: len(new) == 1 and new[0]["booking"] == booking["alice"]["id"] and new[0]["filed_by"] == people["alice"]["id"]
    result, _ = run_case(service, backend, current, "attachment_reference", "alice", values, dict(status="succeeded", outputs_present=["attachment_id"]), own, log)
    started = time.time()
    try:
        outcome = service.repair(result["test_id"], RepairSubmission())
    except AppError as exc:
        outcome = dict(outcome="refused", code=exc.code, message=exc.message)
    log.append(dict(step="missing_capability:live_attempt", seconds=round(time.time() - started), outcome=outcome, version_after=service.view(pid)["version"]))
    if outcome.get("outcome") == "revised":
        return
    view = service.view(pid)
    service.providers = FaultInjection(service.store, n)
    try:
        injected = service.revise(pid, RevisionSubmission(expected_revision=view["review_revision"], instruction="FAULT INJECTION (TEST-ONLY): wire the holder identifier into the booking identifier."), actor="fault_injection")
    finally:
        service.providers = live
    log.append(dict(step="repair:fault_injected", note="Authored defect through the normal revision path; provider recorded as FAULT_INJECTION", result=injected,
                    carried_binding=mapping_check(service, service.view(pid), n)["carried_binding"]))
    if not review(service, pid, log, "repair:review_injected") or not (a := build(service, pid, n, ctx, log, "repair:build_injected")):
        return
    result, new = run_case(service, backend, a, "owner_files_request", "alice", values, dict(status="succeeded"), own, log)
    for _ in range(2):
        if result["verdict"] == "passed":
            break
        verification = f"TEST harness: fixture state read directly shows {len(new)} new service requests after the failed run." if not new else None
        started = time.time()
        try:
            outcome = service.repair(result["test_id"], RepairSubmission(verification=verification))
        except AppError as exc:
            log.append(dict(step="repair:attempt_refused", code=exc.code, message=exc.message, repair_id=exc.details.get("repair_id")))
            if exc.code in PREFLIGHT_REFUSALS:
                return
            continue
        log.append(dict(step="repair:live_attempt", seconds=round(time.time() - started), outcome=outcome, carried_binding=mapping_check(service, service.view(pid), n)["carried_binding"],
                        change_summary=service.view(pid)["change_summary"]))
        if outcome["outcome"] != "revised" or not review(service, pid, log, f"repair:review_v{outcome['version']}") or not (a := build(service, pid, n, ctx, log, f"repair:build_v{outcome['version']}")):
            return
        result, new = run_case(service, backend, a, "owner_files_request", "alice", values, dict(status="succeeded"), own, log)
    if result["verdict"] != "passed":
        try:
            service.repair(result["test_id"], RepairSubmission(verification="TEST harness: no new request"))
        except AppError as exc:
            log.append(dict(step="repair:exhausted", code=exc.code, message=exc.message))


if __name__ == "__main__":
    main()
