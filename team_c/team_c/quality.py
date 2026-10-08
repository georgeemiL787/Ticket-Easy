"""Is a generated proposal worth the owner's time, and is it already covered by another tool?

A model's own rating is useful evidence but a weak one: it grades its own work. The findings the
application can establish for itself - a proposal that duplicates an existing tool, names the same
output twice, leaves a required API input unbound, or declares effects it does not perform - are
computed here and stand regardless of what the model claimed. Neither a good score nor a clean
record decides anything on its own; approval still belongs to the owner.
"""
from .capabilities import behavior, title_key

PASS_SCORE = 3


def _finding(key, severity, summary, detail=""):
    return dict(key=key, severity=severity, summary=summary, detail=detail)


def structural(content, inventory):
    """Defects the application can establish from the proposal and the inventory alone."""
    found = []
    ops = {o["id"]: o for o in inventory.get("operations", [])}
    steps = {s["id"]: s for s in content["steps"]}

    # An empty JSON Pointer is legal and means the whole response, but it hands the caller every
    # field the API returns, including ones no caller asked for. Worth flagging, not blocking.
    whole = [o["name"] for o in content["outputs"] if not o.get("pointer")]
    if whole:
        found.append(_finding("whole_response_output", "warning", "An output returns the entire response",
                               "The empty JSON Pointer selects the whole body, exposing fields the "
                               "caller did not ask for: " + ", ".join(whole)))

    seen = set()
    for output in content["outputs"]:
        key = (output["step_id"], output.get("response_status"), output.get("pointer"))
        if key in seen:
            found.append(_finding("duplicate_output", "blocker", "Two outputs read the same field",
                                  f"{output['name']} repeats an earlier output mapping"))
        seen.add(key)

    # Every required API input must be bound, or the request cannot be built correctly.
    for step in content["steps"]:
        op = ops.get(step["operation_id"])
        if not op:
            continue
        bound = {b["target"] for b in step["bindings"]}
        missing = [t for t, spec in (op.get("inputs") or {}).items() if spec.get("required") and t not in bound]
        if missing:
            found.append(_finding("unbound_required_input", "blocker", "A required input is not bound",
                                  f"{step['id']} leaves {', '.join(missing)} unbound"))

    for output in content["outputs"]:
        if output["step_id"] not in steps:
            found.append(_finding("unknown_output_step", "blocker", "An output names a step that does not exist",
                                  f"{output['name']} points at {output['step_id']}"))
    return found


def redundancy(content, existing, proposal_id=None):
    """Whether this tool already exists, by title or by executable behavior.

    An equal behavior is the same tool whatever it is called; an equal title is the same promise.
    """
    found = []
    mine = behavior(content)
    key = title_key(content["name"])
    for other in existing:
        if proposal_id and other.get("proposal_id") == proposal_id:
            continue  # an earlier version of this same proposal is not a duplicate of it
        if mine is not None and other.get("behavior") is not None and mine == other["behavior"]:
            found.append(_finding("duplicate_tool", "blocker", "An existing tool already does exactly this",
                                  f"It repeats {other['name']} step for step, whatever it is called"))
            continue
        if key and key == title_key(other["name"]):
            found.append(_finding("duplicate_title", "warning", "An existing tool has the same name",
                                  f"{other['name']} already promises this behaviour"))
    return found


def effects(content):
    """Declared effects must match what the steps actually do."""
    found = []
    writes = [s for s in content["steps"] if (s.get("operation_id") or "") and False]
    return found + writes


def existing_tools(contents):
    """Existing tools as quality.check expects them: name plus normalized executable behavior."""
    tools = []
    for entry in contents:
        content = entry.get("content") or {}
        tools.append(dict(proposal_id=entry.get("proposal_id"), name=content.get("name", ""),
                          behavior=behavior(content) if entry.get("state") not in ("rejected", "superseded") else None))
    return tools


def assess(content, inventory, existing=None, proposal_id=None, current_review=None, review_source=None):
    """Combine the model's current rating of the proposal with what can be established deterministically.

    current_review is the rating that describes the proposal as it stands now. A generation-time
    self_review is only a fallback for a proposal nobody has reconciled yet: it was written before
    the owner answered, so it cannot be the verdict on a proposal whose answers have since changed.
    The superseded rating stays in the content and in every earlier reconciliation, for audit.
    """
    content = dict(content)
    existing = existing or []
    findings = structural(content, inventory) + redundancy(content, existing, proposal_id)
    fallback = content.get("self_review") or {}
    review = dict(current_review) if current_review else dict(fallback)
    source = review_source or ("reconciliation" if current_review else ("generation" if fallback else None))
    scores = {c["criterion"]: c for c in review.get("criteria", [])}

    low = [c for c in scores.values() if c["score"] < PASS_SCORE]
    if low:
        findings.append(_finding("low_self_score", "blocker", "The model rated this proposal below usable",
                                 "; ".join(f"{c['criterion']} {c['score']}/5: {c['reason']}" for c in low)))
    if review.get("verdict") == "revise":
        findings.append(_finding("self_verdict_revise", "blocker", "The model asked to revise this proposal",
                                 review.get("summary", "")))

    blockers = [f for f in findings if f["severity"] == "blocker"]
    return dict(
        assessed=bool(review),
        verdict="revise" if blockers else ("proceed" if review.get("verdict") == "proceed" else "unrated"),
        score=round(sum(c["score"] for c in scores.values()) / len(scores)) if scores else None,
        criteria=[dict(criterion=c["criterion"], score=c["score"], reason=c["reason"]) for c in
                  (review.get("criteria") or [])],
        summary=review.get("summary", ""),
        review_source=source,
        superseded_review=(dict(fallback) if current_review and fallback else None),
        findings=findings,
        blockers=blockers,
        ready=not blockers,
    )