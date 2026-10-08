"""Compact operation index and deterministic checks for owner tool requests and capability suggestions."""
import json
import re
import unicodedata
from collections import Counter
from .config import AppError

BLOCKING = {"absent_operation", "unsupported_operation", "restricted_operation"}

# A retrieval round shows the model one budget-sized slice of the catalog. Absence is only a claim
# about the API once the searchable catalog has actually been searched, so a request keeps asking for
# further slices until it runs out of operations. This cap is a runaway guard for catalogs so large
# that exhausting them would cost more than the answer is worth; while it binds, the coverage says
# partial_search=true and the owner is told "not found in the searched subset", never "unsupported".
RETRIEVAL_ROUNDS_CAP = 10


def status(op):
    if op.get("proposal_eligible", op.get("supported", False)):
        return "eligible"
    return "restricted" if op.get("exposure", {}).get("classification") == "restricted" else "unsupported"


def relevance(goal):
    """Score how well one index entry serves the owner's request, from the request's own words.

    The goal is the only evidence of what the owner wants, so operations are ranked by how much of
    it they can plausibly serve. This is a ranking heuristic, not a claim about the API: it never
    decides eligibility, and it never drops an operation the budget can hold.
    """
    # Suggestions rank against the owner's recent goals, which arrive as a list.
    text = " ".join(goal) if isinstance(goal, (list, tuple)) else (goal or "")
    words = [w for w in re.split(r"[^a-z0-9]+", text.lower()) if len(w) > 2]
    if not words:
        return lambda entry: 0
    counts = Counter(words)
    # A word the owner repeats is a stronger signal than one mentioned once.
    weight = {w: 1 + 0.5 * (n - 1) for w, n in counts.items()}
    # Longer words carry more meaning than short ones like "get" or "show".
    weight = {w: v * (1 + min(len(w), 12) / 12) for w, v in weight.items()}

    def score(entry):
        text = " ".join(str(entry.get(k) or "") for k in ("path", "summary")).lower()
        hits = sum(weight[w] for w in weight if w in text)
        # A path segment match is stronger than a word appearing in a description.
        segments = [s for s in re.split(r"[^a-z0-9]+", entry.get("path", "").lower()) if s]
        hits += sum(weight[w] * 2 for w in weight if w in segments)
        return hits

    return score


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


def operation_index(inventory, budget_chars, include_ids=None, exclude_ids=(), goal=""):
    """Eligible operations first, then restricted and unsupported ones, until the character budget is spent
    (always at least one, so batches make progress).

    include_ids limits the index to the owner's selected business areas (None: every operation);
    exclude_ids are operations an earlier batch already considered.

    goal ranks operations by relevance to the request before the budget is spent. Filling the index in
    inventory order and truncating silently discards whatever sorts last, so a request for orders on a
    large API was told the orders endpoints did not exist. Relevance only reorders; it never drops an
    eligible operation that the budget can hold.
    """
    order = {"eligible": 0, "restricted": 1, "unsupported": 2}
    scoped = [o for o in inventory["operations"] if include_ids is None or o["id"] in include_ids]
    excluded = set(exclude_ids) & {o["id"] for o in scoped}
    entries = [entry(o) for o in scoped if o["id"] not in excluded]
    if goal:
        wanted = relevance(goal)
        entries.sort(key=lambda e: (order[e["status"]], -wanted(e)))
    else:
        entries.sort(key=lambda e: order[e["status"]])
    index, used = [], 0
    for e in entries:
        size = len(json.dumps(e, ensure_ascii=False))
        if used + size > budget_chars and index:
            break
        index.append(e)
        used += size
    omitted = [e["id"] for e in entries[len(index):]]
    # searched_operations/partial_search are cumulative over every earlier batch: excluded operations
    # were searched, they are simply not in this round's slice. partial is the same question asked of
    # the whole catalog (is anything still unsearched?) rather than of this one slice, so a request
    # that has now searched everything does not keep reporting "partial" just because one slice is
    # smaller than the catalog.
    searched = len(scoped) - len(omitted)
    coverage = dict(total_operations=len(scoped), considered=len(index), considered_ids=[e["id"] for e in index],
                    omitted_ids=omitted, previously_considered=len(excluded), partial=bool(omitted),
                    searched_operations=searched, partial_search=bool(omitted),
                    area_scoped=include_ids is not None, budget_chars=budget_chars, used_chars=used,
                    goal_ranked=bool(goal))
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
    normalized = unicodedata.normalize("NFKC", text).casefold()
    # Preserve letters and numbers in every script, including Arabic combining marks.
    return " ".join("".join(c if c.isalnum() or unicodedata.category(c).startswith("M") else " " for c in normalized).split())


def screen(output, index, existing, prior, count):
    """Keep grounded, non-duplicate suggestions; the category follows the cited evidence, not the model's label.

    A suggestion names operations, not executable behavior, so sharing operations with an existing tool only
    relates them (one endpoint can serve several tools). It is recognized as that tool only when the name
    matches as well; among suggestions, only a repeated title is a duplicate."""
    states = {e["id"]: e["status"] for e in index}
    tools = {x["proposal_id"]: x for x in existing}
    active = [x for x in existing if x["state"] != "rejected"]
    seen_titles = {key for p in prior if (key := title_key(p["title"]))}
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
        sharing = [x for x in active if key and frozenset(x["operation_ids"]) == key] if category != "blocked_by_missing_api" else []
        normalized_title = title_key(d["title"])
        same = next((x for x in sharing if normalized_title and title_key(x["name"]) == normalized_title), None)
        if same:
            recognized.append(dict(title=d["title"], proposal_id=same["proposal_id"], name=same["name"])); continue
        if normalized_title and normalized_title in seen_titles:
            hold("duplicates an earlier suggestion"); continue
        if len(kept) >= count:
            hold("over the requested number of suggestions"); continue
        for x in sharing:
            if x["proposal_id"] not in d["related_proposal_ids"]:
                d["related_proposal_ids"].append(x["proposal_id"])
        if sharing:
            d["shares_operations_with"] = [dict(proposal_id=x["proposal_id"], name=x["name"]) for x in sharing]
        if normalized_title:
            seen_titles.add(normalized_title)
        kept.append(d)
    return kept, withheld, recognized


def behavior(content):
    """The executable behavior of a proposal with its labels normalized; equal values describe the same tool.

    Ordered steps with each binding's target and source; previous-step references by step position and
    response status; configuration bindings by their value rather than their key; runtime arguments by
    order of first use, so renaming an argument changes nothing but feeding one argument to two inputs
    does; output mappings without their names. None when an unset or unreadable configuration value
    leaves the behavior undetermined: such a tool is never treated as a duplicate.
    """
    position = {s["id"]: i for i, s in enumerate(content["steps"])}
    values = {c["key"]: c.get("value_json") for c in content.get("configuration") or []}
    arguments, steps = {}, []
    for s in content["steps"]:
        bindings = []
        for b in sorted(s["bindings"], key=lambda b: b["target"]):
            kind, source = b["kind"], b["reference"]
            if kind == "business_configuration":
                if values.get(source) is None:
                    return None
                try:
                    source = json.dumps(json.loads(values[source]), sort_keys=True)
                except ValueError:
                    return None
            elif kind == "runtime_argument":
                source = arguments.setdefault(source, len(arguments))
            elif kind == "previous_operation_output":
                source = [position.get(b["step_id"], -1), b["response_status"], source]
            bindings.append([b["target"], kind, source])
        steps.append([s["operation_id"], bindings])
    outputs = sorted([position.get(o["step_id"], -1), o["response_status"], o["pointer"]] for o in content["outputs"])
    return [steps, outputs]
