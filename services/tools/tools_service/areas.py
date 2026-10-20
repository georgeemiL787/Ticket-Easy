"""Business areas: base groups taken from the API document itself, optionally organized by the AI.

Areas only decide which operations are shown to the model together; they never change whether an
operation is eligible, restricted or unsupported.
"""
from .capabilities import status
from .config import AppError

SAMPLES = 5
ASSIGN_BATCH = 10


def _segments(path):
    return [s for s in path.split("/") if s]


def _shared_prefix(paths):
    """Leading literal segments (e.g. api/v1) shared by most paths of this document, kept only when the
    paths below them still split into at least two groups; computed per document, never from a word list."""
    lists = [_segments(p) for p in paths]
    for depth in range(max((len(l) for l in lists), default=0) - 1, 0, -1):
        counts = {}
        for l in lists:
            head = tuple(l[:depth])
            if len(head) == depth and not any(s.startswith("{") for s in head):
                counts[head] = counts.get(head, 0) + 1
        if not counts:
            continue
        head, n = max(counts.items(), key=lambda kv: kv[1])
        below = {l[depth] for l in lists if tuple(l[:depth]) == head and len(l) > depth and not l[depth].startswith("{")}
        if n * 2 > len(lists) and len(below) >= 2:
            return head
    return ()


def _tags(op):
    tags = op.get("tags") or (op.get("original") or {}).get("tags") or []
    return [t for t in tags if isinstance(t, str) and t.strip()]


def group_key(op, prefix):
    tags = _tags(op)
    if tags:
        return tags[0].strip()
    segments = _segments(op["path"])
    rest = segments[len(prefix):] if tuple(segments[:len(prefix)]) == prefix else segments
    literal = [s for s in rest if not s.startswith("{")] or [s for s in segments if not s.startswith("{")]
    return literal[0] if literal else "/"


def label(key):
    text = key.replace("_", " ").replace("-", " ").strip() or key
    return text[:1].upper() + text[1:]


def assignments(inventory):
    """{operation id: base group key}."""
    ops = inventory["operations"]
    prefix = _shared_prefix([o["path"] for o in ops])
    return {o["id"]: group_key(o, prefix) for o in ops}


def base_groups(inventory):
    keys = assignments(inventory)
    groups = {}
    for op in inventory["operations"]:
        g = groups.setdefault(keys[op["id"]], dict(key=keys[op["id"]], label=label(keys[op["id"]]), operation_ids=[], counts=dict(eligible=0, restricted=0, unsupported=0), samples=[]))
        g["operation_ids"].append(op["id"])
        g["counts"][status(op)] += 1
        if len(g["samples"]) < SAMPLES:
            g["samples"].append(dict(method=op["method"], path=op["path"], summary=(op.get("summary") or (op.get("original") or {}).get("summary") or "")[:100]))
    return sorted(groups.values(), key=lambda g: g["label"].lower())


def from_groups(groups, source_note):
    """One area per base group; used before the AI organizes them, or when its output is unusable."""
    return [dict(id=f"a{i}", name=g["label"], description="", audience="unknown", reason=source_note, group_keys=[g["key"]]) for i, g in enumerate(groups, 1)]


def named_areas(output):
    """Areas named by the model, in its priority order; names must be distinct because groups are assigned by name."""
    named, seen = [], set()
    for area in output.areas:
        name = area.name.strip()
        if name.lower() in seen:
            raise AppError("invalid_area_grouping", f"Area name {name!r} is used twice")
        seen.add(name.lower())
        named.append(dict(name=name, description=area.description.strip(), audience=area.audience, reason=area.reason.strip()))
    return named


def assignment(output, groups, named):
    """{group key: area name} for one batch of groups: every group exactly once, only named areas."""
    keys, names = {g["key"] for g in groups}, {a["name"] for a in named}
    if set(output.assignments) != keys:
        raise AppError("invalid_area_grouping", "The AI did not assign exactly the supplied API groups")
    wrong = sorted(k for k, v in output.assignments.items() if v not in names)
    if wrong:
        raise AppError("invalid_area_grouping", "API groups assigned to an unknown area: " + ", ".join(wrong[:10]))
    return dict(output.assignments)


def from_assignments(named, assigned, groups):
    """Areas in priority order with their groups; areas that received no group are dropped."""
    areas = []
    for a in named:
        keys = [g["key"] for g in groups if assigned[g["key"]] == a["name"]]
        if keys:
            areas.append(dict(id=f"a{len(areas) + 1}", **a, group_keys=keys))
    return areas


def model_groups(groups, samples=SAMPLES):
    """Compact base-group description sent to the model; samples=0 sends names and counts only."""
    return [dict(key=g["key"], label=g["label"], operations=len(g["operation_ids"]), **g["counts"], **({"samples": g["samples"][:samples]} if samples else {})) for g in groups]
