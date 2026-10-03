"""Sandbox test evaluation, structured failure reports and the guard on model repairs.

Expectations are authored by the operator/test author, never by the model. A repair may only change
how existing operations are wired (bindings and output mappings); anything else needs an owner revision.
"""
import json
from .config import AppError

MAX_ATTEMPTS = 2
PREFLIGHT = {
    "approval_not_current": ("approval", "Review and approve the current version, then rebuild."),
    "enforcement_not_current": ("configuration", "Review the current enforcement configuration and rebuild."),
    "access_enforcement_missing": ("configuration", "Configure and review an enforceable mechanism for every access requirement."),
    "invalid_arguments": ("test_input", "Correct the test arguments; the tool contract is not changed to fit a test."),
}
NOT_REPAIRABLE = {
    "outcome_unknown": ("uncertain_write", "A write may have been applied. Check the target state manually; it is never retried or repaired automatically."),
    "partial": ("partial_write", "An earlier write was applied. Inspect and restore the target state manually before any change."),
    "connector_auth": ("authentication", "Fix the connector credentials or authentication configuration; the proposal is not rewritten for this."),
    "blocked_by_access_check": ("access_check", "Access checks are not repaired automatically; review the enforcement configuration or the test identity."),
    "redirect_rejected": ("destination", "Review the connector destination; redirects are never followed."),
    "not_sent": ("connectivity", "The target was unreachable; check the connector, not the proposal."),
    "no_response": ("connectivity", "No complete response arrived; check the target and its state."),
}
MAPPING = {"invalid_binding", "rejected_by_api", "invalid_response", "invalid_request"}


def evaluate(artifact, content, name, expect, report, outputs):
    """(verdict, failure_report or None). Values from the target never enter the report, only names and codes."""
    failure = report.get("failure") or {}
    mismatches = []
    if report["status"] != expect["status"]:
        mismatches.append(f'status: expected {expect["status"]}, got {report["status"]}')
    if expect.get("failure_step") and failure.get("step_id") != expect["failure_step"]:
        mismatches.append(f'failure step: expected {expect["failure_step"]}, got {failure.get("step_id")}')
    if expect.get("failure_outcome") and failure.get("outcome") != expect["failure_outcome"]:
        mismatches.append(f'failure outcome: expected {expect["failure_outcome"]}, got {failure.get("outcome")}')
    missing = sorted({n for n in list(expect.get("outputs", {})) + expect.get("outputs_present", []) if n not in outputs})
    differing = sorted(n for n, v in expect.get("outputs", {}).items() if n in outputs and outputs[n] != v)
    mismatches += [f"output {n}: missing" for n in missing] + [f"output {n}: differs from the expected value" for n in differing]
    if not mismatches:
        return "passed", None
    trace = [{k: e.get(k) for k in ("step_id", "method", "path", "http_status", "outcome", "write_state")} for e in report.get("trace", [])]
    step = next((s for s in artifact["steps"] if s["id"] == failure.get("step_id")), None)
    proposal_step = next((s for s in content["steps"] if step and s["id"] == step["id"]), None)
    kind, repairable, action, verify = classify(expect, report, failure, trace, missing + differing)
    return "failed", dict(
        case=name, artifact_id=None, expected=expect, mismatches=mismatches,
        actual=dict(status=report["status"], code=report.get("code"), failure=failure or None, outputs_present=sorted(outputs), trace=trace),
        step=dict(id=step["id"], method=step["method"], path=step["path"], operation_id=step["operation_id"]) if step else None,
        bindings=proposal_step["bindings"] if proposal_step else [],
        output_mappings=[o for o in content["outputs"] if o["name"] in missing + differing],
        classification=kind, repairable=repairable, requires_verification=verify, recommended_action=action)


def classify(expect, report, failure, trace, output_problems):
    status, outcome = report["status"], failure.get("outcome")
    if status == "rejected":
        kind, action = PREFLIGHT.get(report.get("code"), ("configuration", "Fix the trusted execution configuration (connector, identity, destination)."))
        return kind, False, action, False
    if expect["status"] == "failed" and status == "succeeded":
        return "expected_denial_not_observed", False, "A run expected to be refused succeeded. Treat this as an access defect for owner review; it is not repaired automatically.", False
    for key in (status, failure.get("step_id"), outcome):
        if key in NOT_REPAIRABLE:
            kind, action = NOT_REPAIRABLE[key]
            return kind, False, action, False
    http = next((e["http_status"] for e in trace if e["step_id"] == failure.get("step_id")), None)
    if outcome == "rejected_by_api" and http in (401, 403):
        return "access_or_authentication", False, "The target refused the credential or access; configure or clarify access instead of rewriting mappings.", False
    if outcome in MAPPING or (status == "succeeded" and output_problems):
        rejected_write = any(e["step_id"] == failure.get("step_id") and e["write_state"] == "rejected_unverified" for e in trace)
        return "mapping_suspect", True, "Repair the bindings or output mappings of the existing operations, then review, rebuild and rerun.", rejected_write
    return "unclassified", False, "Inspect the failure manually.", False


def describe(report):
    """One line: where the test failed, how, and the wiring of the failing step (argument names only, never values)."""
    failure, step = (report["actual"] or {}).get("failure") or {}, report.get("step")
    where = f'{step["id"]} {step["method"]} {step["path"]}' if step else failure.get("step_id") or "outputs"
    wiring = "; ".join(f'{b["target"]} <- {b["kind"]} {b["reference"]}' + (f' from {b["step_id"]} {b["response_status"]}' if b.get("step_id") else "") for b in report["bindings"])
    return f'{where}: {failure.get("outcome") or report["actual"]["status"]} {failure.get("detail") or ""}'.rstrip() + f' ({"; ".join(report["mismatches"])})' + (f"; wiring: {wiring}" if wiring else "")


def evidence(report, content, ops):
    """Contract facts needed to understand the failure; all from the stored inventory, none from target responses."""
    steps = {s["id"]: s for s in content["steps"]}
    step = report.get("step")
    if not step:
        return dict(step_response_schemas={f'{s["id"]} {ops[s["operation_id"]]["method"]} {ops[s["operation_id"]]["path"]} {code}': r["schema"]
                                           for s in content["steps"] for code, r in sorted(ops[s["operation_id"]]["responses"].items()) if code.startswith("2") and r.get("schema")})
    op = ops[step["operation_id"]]
    http = next((e["http_status"] for e in report["actual"]["trace"] if e["step_id"] == step["id"]), None)
    sources = {}
    for b in report["bindings"]:
        if b["kind"] == "previous_operation_output" and b["step_id"] in steps:
            src = ops[steps[b["step_id"]]["operation_id"]]
            sources[f'{b["step_id"]} {src["method"]} {src["path"]} {b["response_status"]}'] = (src["responses"].get(b["response_status"]) or {}).get("schema")
    return dict(failing_operation=f'{op["method"]} {op["path"]}', observed_status=http,
                documented_meaning_of_observed_status=(op["responses"].get(str(http)) or {}).get("description") if http else None,
                bound_input_schemas={b["target"]: op["inputs"][b["target"]]["schema"] for b in report["bindings"] if b["target"] in op["inputs"]},
                source_response_schemas=sources)


def feedback(exc, report):
    """Why a model attempt was not accepted, for the history the next attempt receives."""
    changes = exc.details.get("candidate_changes")
    if exc.code == "revision_not_changed":
        remaining = "The candidate is identical to the failing version, so the failure persists: " + describe(report)
    else:
        remaining = "; ".join(exc.details.get("errors") or []) or exc.message
    return (f"{exc.code}: " + (f'candidate changes: {"; ".join(changes) or "none"}. ' if changes is not None else "no candidate proposal. ") + f"Remaining error: {remaining}")[:2000]


def instruction(report, contract, history):
    text = ("AUTOMATED REPAIR REQUEST generated by Team C from a failed sandbox test (not an owner instruction). "
            "Fix only how the existing steps are wired: step bindings and output mappings. Keep the same steps, operations, questions and configuration. "
            "Do not add runtime inputs for identity, ownership or permissions. Choose each previous_operation_output pointer by meaning, comparing the "
            "target input with every field of the source response. Return outcome revised with the complete corrected proposal; cannot_repair if no "
            "rewiring of the existing steps can meet the expected behavior; capability_gap if it needs an operation or response field the inventory "
            "does not provide. ")
    parts = dict(failure_report={k: report[k] for k in ("case", "expected", "mismatches", "actual", "step", "bindings", "output_mappings")}, contract_evidence=contract)
    if history:
        text += "Earlier attempts for this failure were not accepted (see earlier_attempts); do not return the same wiring again. "
        parts["earlier_attempts"] = history
    return text + json.dumps(parts, ensure_ascii=False, sort_keys=True)


def check_scope(old, new):
    """A repair keeps operations, questions, configuration and the runtime-argument set; it only rewires them."""
    def runtime(content):
        return {b["reference"] for s in content["steps"] for b in s["bindings"] if b["kind"] == "runtime_argument"}
    def trusted(content):
        return sorted((s["id"], b["target"], b["reference"]) for s in content["steps"] for b in s["bindings"] if b["kind"] == "trusted_application_context")
    problems = []
    if [(s["id"], s["operation_id"]) for s in old["steps"]] != [(s["id"], s["operation_id"]) for s in new["steps"]]:
        problems.append("steps or operations changed")
    if old["questions"] != new["questions"] or old["configuration"] != new["configuration"]:
        problems.append("questions or configuration changed")
    if not runtime(new) <= runtime(old):
        problems.append("new runtime inputs: " + ", ".join(sorted(runtime(new) - runtime(old))))
    if trusted(old) != trusted(new):
        problems.append("trusted context bindings changed")
    if problems:
        raise AppError("repair_out_of_scope", "A repair may only rewire existing operations; this needs an owner revision: " + "; ".join(problems))
