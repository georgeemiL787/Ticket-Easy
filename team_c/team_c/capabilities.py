"""Compact operation index and deterministic checks for owner tool requests and capability suggestions."""
import json
import re
from .config import AppError

BLOCKING = {"absent_operation", "unsupported_operation", "restricted_operation"}


def status(op):
    if op.get("proposal_eligible", op.get("supported", False)):
        return "eligible"
    return "restricted" if op.get("exposure", {}).get("classification") == "restricted" else "unsupported"


def reason(op):
    """Short plain-language reason an operation cannot be used for tools; None when it is eligible."""
    state = status(op)
    if state == "eligible":
        return None
    if state == "restricted":
        signal = next((s for s in op.get("exposure", {}).get("signals", []) if s.get("effect") == "restricts"), None)
        return signal["detail"] if signal else "restricted"
    errors = (op.get("binding") or {}).get("errors") or []
    if not errors:
        return "the whole document was rejected" if "exposure" not in op else "not supported"
    if errors[0].startswith("unsupported_media"):
        media = set(((op.get("original") or {}).get("requestBody") or {}).get("content") or {})
        if "multipart/form-data" in media:
            return "file upload (multipart/form-data)"
        if "application/x-www-form-urlencoded" in media:
            return "form data (application/x-www-form-urlencoded)"
    return errors[0].split(":", 1)[0].replace("_", " ")


def entry(op):
    success = next((r for c, r in sorted(op.get("responses", {}).items()) if c.startswith("2") and r.get("schema")), None)
    returns = sorted((success or {}).get("schema", {}).get("properties", {}))[:12] if success else []
    return dict(id=op["id"], method=op["method"], path=op["path"], summary=(op.get("summary") or op.get("description") or "")[:160],
                status=status(op), auth=op.get("declared_auth", {}).get("status"), inputs=sorted(op.get("inputs", {}))[:16], returns=returns)


def operation_index(inventory, budget_chars, include_ids=None, exclude_ids=()):
    """Eligible operations first, then restricted and unsupported ones, until the character budget is spent
    (always at least one, so batches make progress).

    include_ids limits the index to the owner's selected business areas (None: every operation);
    exclude_ids are operations an earlier batch already considered.
    """
    order = {"eligible": 0, "restricted": 1, "unsupported": 2}
    scoped = [o for o in inventory["operations"] if include_ids is None or o["id"] in include_ids]
    excluded = set(exclude_ids) & {o["id"] for o in scoped}
    entries = sorted((entry(o) for o in scoped if o["id"] not in excluded), key=lambda e: order[e["status"]])
    index, used = [], 0
    for e in entries:
        size = len(json.dumps(e, ensure_ascii=False))
        if used + size > budget_chars and index:
            break
        index.append(e)
        used += size
    omitted = [e["id"] for e in entries[len(index):]]
    coverage = dict(total_operations=len(scoped), considered=len(index), considered_ids=[e["id"] for e in index],
                    omitted_ids=omitted, previously_considered=len(excluded), partial=len(index) < len(scoped),
                    area_scoped=include_ids is not None, budget_chars=budget_chars, used_chars=used)
    return index, coverage


def model_coverage(coverage):
    return {k: v for k, v in coverage.items() if k not in ("considered_ids", "omitted_ids", "all_considered_ids")}


def missing_problem(m, states):
    if m["kind"] == "absent_operation" and m["operation_ids"]:
        return "calls an operation absent although the index contains it"
    wanted = {"unsupported_operation": "unsupported", "restricted_operation": "restricted"}.get(m["kind"])
    if wanted and (not m["operation_ids"] or any(states.get(i) != wanted for i in m["operation_ids"])):
        return f'cites operations that are not {wanted}'
    if m["kind"] == "missing_information" and any(i not in states for i in m["operation_ids"]):
        return "cites an operation outside the index"
    return None


def check_triage(t, index, existing):
    states = {e["id"]: e["status"] for e in index}
    bad = lambda message: AppError("invalid_request_triage", message)
    if any(states.get(i) != "eligible" for i in t.operation_ids):
        raise bad("A selected operation is not an eligible operation of the index; restricted and unsupported operations cannot be used")
    if set(t.existing_proposal_ids) - {x["proposal_id"] for x in existing}:
        raise bad("The triage cites an unknown existing tool")
    for m in t.missing:
        problem = missing_problem(m.model_dump(), states)
        if problem:
            raise bad("A missing capability " + problem)
    required = dict(feasible=t.operation_ids, needs_clarification=t.questions, existing_tool=t.existing_proposal_ids,
                    unavailable=[m for m in t.missing if m.kind in BLOCKING])
    if not required[t.outcome]:
        raise bad(f"A {t.outcome} outcome must name its " + dict(feasible="operations", needs_clarification="questions",
                  existing_tool="existing tools", unavailable="absent, unsupported or restricted capability")[t.outcome])


def title_key(text):
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def screen(output, index, existing, prior, count):
    """Keep grounded, non-duplicate suggestions; the category follows the cited evidence, not the model's label."""
    states = {e["id"]: e["status"] for e in index}
    tools = {x["proposal_id"]: x for x in existing}
    active = {frozenset(x["operation_ids"]): x["proposal_id"] for x in existing if x["state"] != "rejected"}
    seen_titles = {title_key(p["title"]) for p in prior}
    seen_sets = {(frozenset(p["operation_ids"]), p["category"]) for p in prior if p["operation_ids"]}
    kept, withheld, recognized = [], [], []
    for s in output.suggestions:
        d = s.model_dump()
        def hold(reason):
            withheld.append(dict(title=d["title"], operation_ids=d["operation_ids"], reason=reason))
        if any(i not in states for i in d["operation_ids"]):
            hold("cites an operation outside the reviewed index"); continue
        if set(d["related_proposal_ids"]) - set(tools):
            hold("cites an unknown existing tool"); continue
        if any(states[i] == "restricted" for i in d["operation_ids"]):
            hold("uses a restricted operation; restricted operations stay restricted"); continue
        problems = [p for p in (missing_problem(m, states) for m in d["missing"]) if p]
        if problems:
            hold("a missing capability " + problems[0]); continue
        for i in d["operation_ids"]:
            if states[i] == "unsupported" and not any(i in m["operation_ids"] for m in d["missing"]):
                d["missing"].append(dict(kind="unsupported_operation", description=f'{i} is not executable by Team C', operation_ids=[i]))
        kinds = {m["kind"] for m in d["missing"]}
        category = "blocked_by_missing_api" if kinds & BLOCKING else "needs_clarification" if "missing_information" in kinds else "feasible"
        if category == "feasible" and s.category != "feasible":
            hold(f"labelled {s.category} without naming what is missing"); continue
        if category != "blocked_by_missing_api" and not d["operation_ids"]:
            hold("names no supporting operation"); continue
        if category != s.category:
            d["category_adjusted_from"] = s.category
        d["category"] = category
        key = frozenset(d["operation_ids"])
        if category != "blocked_by_missing_api" and key in active:
            recognized.append(dict(title=d["title"], proposal_id=active[key], name=tools[active[key]]["name"])); continue
        if title_key(d["title"]) in seen_titles or (key and (key, category) in seen_sets):
            hold("duplicates an earlier suggestion"); continue
        if len(kept) >= count:
            hold("over the requested number of suggestions"); continue
        seen_titles.add(title_key(d["title"]))
        if key:
            seen_sets.add((key, category))
        kept.append(d)
    return kept, withheld, recognized


def signature(content):
    """Steps with their operations and binding kinds; equal signatures describe the same tool behavior."""
    return [[s["operation_id"], sorted(f'{b["target"]}<-{b["kind"]}' for b in s["bindings"])] for s in content["steps"]]
