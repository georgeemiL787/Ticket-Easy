"""Two saved evaluation runs side by side (`python -m agent_service eval-compare A.json B.json`).

A run is the JSON that `python -m agent_service eval` writes. For every measure the table shows the first run, the
second run, and the difference (second minus first), for all conversations and for each language style. The sign is not
judged here: the "better" column says which direction is better, so a drop in "needless handoffs" reads as an
improvement.
"""

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from agent_service.judge import CRITERIA

STYLES = ("en", "ar", "mixed", "arabizi")
Getter = Callable[[Mapping[str, Any]], float | None]


def _top(key: str) -> Getter:
    return lambda scope: _number(scope.get(key))


def _measure(key: str) -> Getter:
    return lambda scope: _number((scope.get("measures") or {}).get(key))


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


# (name, how to read it, how to show it, which direction is better)
MEASURES: tuple[tuple[str, Getter, str, str], ...] = (
    ("solved without a person", _measure("solved_without_person_rate"), "pct", "higher"),
    ("needless handoffs", _measure("needless_handoffs"), "count", "lower"),
    ("missed escalations", _measure("missed_escalations"), "count", "lower"),
    ("wrong or unapproved actions", _measure("wrong_or_unapproved_actions"), "count", "lower (target 0)"),
    ("unbacked facts sent", _measure("unbacked_facts"), "count", "lower (target 0)"),
    ("agent gave up (fallbacks)", _measure("agent_fallbacks"), "count", "lower"),
    ("AI calls per message", _measure("ai_calls_per_message"), "float", "lower"),
    ("time per reply p50 (ms)", _measure("time_p50_ms"), "ms", "lower"),
    ("time per reply p95 (ms)", _measure("time_p95_ms"), "ms", "lower"),
    ("intent accuracy", _top("intent_accuracy"), "pct", "higher"),
    ("decision accuracy", _top("decision_accuracy"), "pct", "higher"),
    ("citation accuracy", _top("citation_accuracy"), "pct", "higher"),
    ("locale match", _top("locale_match"), "pct", "higher"),
    ("handoff precision", _top("handoff_precision"), "pct", "higher"),
    ("handoff recall", _top("handoff_recall"), "pct", "higher"),
)


def load_run(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "overall" not in data or "styles" not in data:
        raise SystemExit(f"{path} is not a report written by `python -m agent_service eval`")
    return data


def _show(value: float | None, kind: str) -> str:
    if value is None:
        return "—"
    if kind == "pct":
        return f"{value * 100:.1f}%"
    if kind == "ms":
        return f"{value:.0f}"
    if kind == "float":
        return f"{value:.2f}"
    return f"{value:.0f}"


def _difference(a: float | None, b: float | None, kind: str) -> str:
    if a is None or b is None:
        return "—"
    delta = b - a
    if kind == "pct":
        return f"{delta * 100:+.1f} pts"
    if kind == "float":
        return f"{delta:+.2f}"
    return f"{delta:+.0f}"


def judge_mean(run: Mapping[str, Any], scope: str) -> float | None:
    """The AI judge's mean grade over its four criteria, when the run was judged (`eval --judge`)."""
    stats = (run.get("judge") or {}).get(scope)
    if not stats:
        return None
    means = [_number(stats.get(f"mean_{name}")) for name in CRITERIA]
    present = [m for m in means if m is not None]
    return sum(present) / len(present) if present else None


def _table(title: str, first: Mapping[str, Any], second: Mapping[str, Any], scope: str, a: str, b: str) -> list[str]:
    scope_a = first["overall"] if scope == "all" else first["styles"][scope]
    scope_b = second["overall"] if scope == "all" else second["styles"][scope]
    lines = [
        f"### {title}",
        "",
        f"| measure | {a} | {b} | difference | better |",
        "|---|---|---|---|---|",
    ]
    for name, getter, kind, better in MEASURES:
        x, y = getter(scope_a), getter(scope_b)
        lines.append(f"| {name} | {_show(x, kind)} | {_show(y, kind)} | {_difference(x, y, kind)} | {better} |")
    x, y = judge_mean(first, scope), judge_mean(second, scope)
    quality = f"{_show(x, 'float')} | {_show(y, 'float')} | {_difference(x, y, 'float')}"
    lines.append(f"| reply quality (AI judge, 1-5) | {quality} | higher |")
    return lines + [""]


def compare_runs(
    first: Mapping[str, Any], second: Mapping[str, Any], name_a: str = "first", name_b: str = "second"
) -> str:
    """The two runs as markdown: one table for everything, then one per language style."""
    head = [
        f"# Evaluation compared: {name_a} vs {name_b}",
        "",
        f"- {name_a}: mode {first.get('mode')}, {first.get('date')}, label {first.get('label') or '-'}, "
        f"{first['overall'].get('conversations')} conversations",
        f"- {name_b}: mode {second.get('mode')}, {second.get('date')}, label {second.get('label') or '-'}, "
        f"{second['overall'].get('conversations')} conversations",
        "",
    ]
    body = _table("All conversations", first, second, "all", name_a, name_b)
    for style in STYLES:
        if style in first["styles"] and style in second["styles"]:
            body += _table(f"Language style: {style}", first, second, style, name_a, name_b)
    return "\n".join(head + body)
