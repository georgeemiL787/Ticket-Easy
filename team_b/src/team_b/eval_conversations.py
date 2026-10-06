"""Measure the whole agent on short conversations, separately for English, Egyptian Arabic, mixed and Arabizi.

Each conversation in eval/conversations/*.jsonl is run turn by turn through the real brain (stand-ins for the shop and
the policy search) and every turn is compared with its gold labels:

- intent accuracy: the first intent understood equals the first gold intent ("none" when the gold list is empty);
- decision accuracy: the decision (answer, clarify, handoff, ...) is the gold one, or one of several allowed ones;
- citation accuracy: an answer cites at least one of the passages the gold allows;
- locale match: the reply is in the style of the customer (English, Arabic or Arabizi);
- handoff precision and recall: handoffs made versus handoffs the gold wanted;
- entity recall: the gold details (order id) were read;
- latency p50 and p95 per style, and the conversations that went wrong most, with their first wrong turn.

The gold labels come from the policies and business rules, not from the brain (see scripts/make_eval_set.py).
"""

import json
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from team_b.brain.metrics import percentile
from team_b.config import PROJECT_ROOT, Settings
from team_b.container import Container, build_container
from team_b.domain.base import FrozenModel
from team_b.judge import CRITERIA, Judge, JudgedTurn, JudgeStats

CONVERSATIONS_DIR = PROJECT_ROOT / "eval" / "conversations"
BASELINE_PATH = PROJECT_ROOT / "eval" / "eval_baseline.json"
TENANT = "shop_001"
EVAL_TODAY = date(2026, 9, 28)  # the demo shop's orders are dated against this day
STYLES = ("en", "ar", "mixed", "arabizi")
LOCALE_OF_STYLE = {"en": "en", "ar": "ar", "mixed": "ar", "arabizi": "arabizi"}  # mixed customers get Egyptian Arabic
WORST = 10
METRICS = ("intent", "decision", "citation", "locale")
Style = Literal["en", "ar", "mixed", "arabizi"]


class Gold(FrozenModel):
    intents: tuple[str, ...] | None = None  # None: not checked; (): no intent expected
    entities: dict[str, str] | None = None
    decision: str | tuple[str, ...] | None = None  # one decision, or the ones that are all acceptable
    citations_any: tuple[str, ...] = ()
    escalation: str | None = None
    text_contains: str | None = None


class EvalTurn(FrozenModel):
    say: str = Field(min_length=1)
    gold: Gold = Field(default_factory=Gold)


class EvalConversation(FrozenModel):
    id: str = Field(min_length=1)
    language_style: Style
    topic: str = ""
    turns: tuple[EvalTurn, ...] = Field(min_length=1)


def load_set(directory: Path = CONVERSATIONS_DIR) -> list[EvalConversation]:
    found: list[EvalConversation] = []
    for path in sorted(directory.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                found.append(EvalConversation.model_validate(json.loads(line)))
    ids = [c.id for c in found]
    if len(ids) != len(set(ids)):
        raise ValueError("conversation ids must be unique")
    return found


def sample(conversations: Sequence[EvalConversation], per_style: int) -> list[EvalConversation]:
    """A small, even sample: `per_style` conversations of every style, spread across the set (not the first ones)."""
    chosen: list[EvalConversation] = []
    for style in STYLES:
        rows = [c for c in conversations if c.language_style == style]
        stride = max(1, len(rows) // per_style)
        chosen += rows[::stride][:per_style]
    return chosen


# ---- running ----


@dataclass
class TurnResult:
    conversation: str
    style: str
    index: int
    said: str
    decision: str
    escalation: str | None
    locale: str
    citations: tuple[str, ...]
    intent: str  # the first intent understood, or "none"
    entities: dict[str, str]
    text: str
    latency_ms: float
    wrong: list[str] = field(default_factory=list)  # which checks failed, with what was expected and what happened


@dataclass
class Counter:
    right: int = 0
    total: int = 0

    def add(self, ok: bool) -> None:
        self.total += 1
        self.right += int(ok)

    @property
    def rate(self) -> float | None:
        return self.right / self.total if self.total else None


def decisions_allowed(gold: Gold) -> tuple[str, ...]:
    if gold.decision is None:
        return ()
    return (gold.decision,) if isinstance(gold.decision, str) else gold.decision


async def run_conversation(container: Container, conversation: EvalConversation) -> list[TurnResult]:
    assert container.orchestrator is not None
    results: list[TurnResult] = []
    chat_id = f"eval-{conversation.id}"
    for index, turn in enumerate(conversation.turns):
        started = time.perf_counter()
        reply = await container.orchestrator.handle_turn(TENANT, chat_id, turn.say)
        latency = (time.perf_counter() - started) * 1000
        trace = await container.traces.get(TENANT, reply.trace_id)
        assert trace is not None
        escalation = trace.escalation_reason.value if trace.escalation_reason else None
        result = TurnResult(
            conversation=conversation.id, style=conversation.language_style, index=index, said=turn.say,
            decision=reply.decision.value, escalation=escalation,
            locale=reply.locale.value, citations=tuple(reply.citations),
            intent=trace.intents[0].name if trace.intents else "none", entities=dict(trace.entities), text=reply.text,
            latency_ms=latency,
        )  # fmt: skip
        result.wrong = problems(turn.gold, result, LOCALE_OF_STYLE[conversation.language_style])
        results.append(result)
    return results


def problems(gold: Gold, got: TurnResult, locale: str) -> list[str]:
    """What is wrong in this turn: one short text per failed check."""
    wrong: list[str] = []
    if gold.intents is not None and got.intent != (gold.intents[0] if gold.intents else "none"):
        wrong.append(f"intent: expected {gold.intents[0] if gold.intents else 'none'}, got {got.intent}")
    allowed = decisions_allowed(gold)
    if allowed and got.decision not in allowed:
        wrong.append(f"decision: expected {'/'.join(allowed)}, got {got.decision}")
    if gold.citations_any and not set(gold.citations_any) & set(got.citations):
        wrong.append(
            f"citation: expected one of {', '.join(gold.citations_any)}, got {', '.join(got.citations) or 'none'}"
        )
    if gold.escalation is not None and got.escalation != gold.escalation:
        wrong.append(f"escalation: expected {gold.escalation}, got {got.escalation}")
    if gold.entities and any(got.entities.get(k) != v for k, v in gold.entities.items()):
        wrong.append(f"entities: expected {gold.entities}, got {got.entities}")
    if gold.text_contains and gold.text_contains not in got.text:
        wrong.append(f"text: expected it to contain {gold.text_contains}")
    if got.locale != locale:
        wrong.append(f"locale: expected {locale}, got {got.locale}")
    return wrong


# ---- the report ----


@dataclass
class StyleReport:
    style: str
    conversations: int = 0
    turns: int = 0
    intent: Counter = field(default_factory=Counter)
    decision: Counter = field(default_factory=Counter)
    citation: Counter = field(default_factory=Counter)
    locale: Counter = field(default_factory=Counter)
    entities: Counter = field(default_factory=Counter)
    handoff_made: int = 0
    handoff_wanted: int = 0
    handoff_right: int = 0
    latencies: list[float] = field(default_factory=list)

    def add(self, gold: Gold, got: TurnResult, locale: str) -> None:
        self.turns += 1
        self.latencies.append(got.latency_ms)
        self.locale.add(got.locale == locale)
        if gold.intents is not None:
            self.intent.add(got.intent == (gold.intents[0] if gold.intents else "none"))
        allowed = decisions_allowed(gold)
        if allowed:
            self.decision.add(got.decision in allowed)
            wanted, made = "handoff" in allowed, got.decision == "handoff"
            self.handoff_wanted += int(wanted)
            self.handoff_made += int(made)
            self.handoff_right += int(wanted and made)
        if gold.citations_any:
            self.citation.add(bool(set(gold.citations_any) & set(got.citations)))
        if gold.entities:
            for key, value in gold.entities.items():
                self.entities.add(got.entities.get(key) == value)

    @property
    def handoff_precision(self) -> float | None:
        return self.handoff_right / self.handoff_made if self.handoff_made else None

    @property
    def handoff_recall(self) -> float | None:
        return self.handoff_right / self.handoff_wanted if self.handoff_wanted else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "style": self.style, "conversations": self.conversations, "turns": self.turns,
            "intent_accuracy": self.intent.rate, "decision_accuracy": self.decision.rate,
            "citation_accuracy": self.citation.rate, "locale_match": self.locale.rate,
            "entity_recall": self.entities.rate,
            "handoff_precision": self.handoff_precision, "handoff_recall": self.handoff_recall,
            "latency_p50_ms": percentile(self.latencies, 0.5), "latency_p95_ms": percentile(self.latencies, 0.95),
        }  # fmt: skip


@dataclass
class EvalReport:
    mode: str
    when: date
    styles: dict[str, StyleReport]
    overall: StyleReport
    worst: list[dict[str, Any]]
    topics: dict[str, Counter] = field(default_factory=dict)  # topic -> conversations with every check right
    judge: dict[str, JudgeStats] | None = None  # the AI judge's advice per style (never part of pass or fail)
    judged: list[JudgedTurn] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode, "date": self.when.isoformat(),
            "topics": {t: {"right": c.right, "total": c.total} for t, c in sorted(self.topics.items())},
            "judge": None if self.judge is None else {s: j.as_dict() for s, j in self.judge.items()},
            "overall": self.overall.as_dict(), "styles": {s: r.as_dict() for s, r in self.styles.items()},
            "worst_conversations": self.worst,
        }  # fmt: skip


async def evaluate(
    conversations: Sequence[EvalConversation],
    settings: Settings,
    *,
    mode: str = "rules",
    when: date | None = None,
    judge: Judge | None = None,
) -> EvalReport:
    """Run the conversations on one container (each in its own chat) and measure every turn."""
    container = build_container(settings.model_copy(update={"fixed_today": EVAL_TODAY, "store": "memory"}))
    styles = {s: StyleReport(s) for s in STYLES}
    overall = StyleReport("all")
    scored: list[tuple[int, str, dict[str, Any]]] = []
    topics: dict[str, Counter] = defaultdict(Counter)
    judge_stats = {s: JudgeStats() for s in (*STYLES, "all")} if judge is not None else None
    judged: list[JudgedTurn] = []
    for conversation in conversations:
        results = await run_conversation(container, conversation)
        locale = LOCALE_OF_STYLE[conversation.language_style]
        report = styles[conversation.language_style]
        report.conversations += 1
        overall.conversations += 1
        for turn, got in zip(conversation.turns, results, strict=True):
            report.add(turn.gold, got, locale)
            overall.add(turn.gold, got, locale)
        if judge is not None and judge_stats is not None:
            for turn, got in zip(conversation.turns, results, strict=True):
                score = await judge.score(turn.say, got.text, conversation.language_style)
                judge_stats[conversation.language_style].add(score)
                judge_stats["all"].add(score)
                if score is not None:
                    judged.append(
                        JudgedTurn(conversation.id, conversation.language_style, got.index, turn.say, got.text, score)
                    )
        wrong_turns = [r for r in results if r.wrong]
        topics[conversation.topic or "-"].add(not wrong_turns)
        if wrong_turns:
            first = wrong_turns[0]
            scored.append(
                (
                    sum(len(r.wrong) for r in wrong_turns),
                    conversation.id,
                    {
                        "id": conversation.id,
                        "style": conversation.language_style,
                        "topic": conversation_topic(conversation),
                        "wrong_checks": sum(len(r.wrong) for r in wrong_turns),
                        "first_wrong_turn": first.index + 1,
                        "said": first.said,
                        "problems": first.wrong,
                        "reply": first.text[:160],
                    },
                )  # fmt: skip
            )
    scored.sort(key=lambda item: (-item[0], item[1]))
    final = EvalReport(mode, when or date.today(), styles, overall, [row for _, _, row in scored[:WORST]], dict(topics))
    final.judge, final.judged = judge_stats, judged
    return final


def conversation_topic(conversation: EvalConversation) -> str:
    return conversation.topic or "-"


# ---- showing and recording ----


def pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def ms(value: float | None) -> str:
    return "—" if value is None else f"{value:.0f}"


def render(report: EvalReport) -> str:
    rows = [*report.styles.values(), report.overall]
    lines = [
        f"# Conversation evaluation · {report.mode} · {report.when.isoformat()}",
        "",
        "| style | conversations | turns | intent | decision | citation | locale "
        "| handoff precision | handoff recall | p50 ms | p95 ms |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.style} | {r.conversations} | {r.turns} | {pct(r.intent.rate)} | {pct(r.decision.rate)} "
            f"| {pct(r.citation.rate)} | {pct(r.locale.rate)} | {pct(r.handoff_precision)} | {pct(r.handoff_recall)} "
            f"| {ms(percentile(r.latencies, 0.5))} | {ms(percentile(r.latencies, 0.95))} |"
        )
    lines += [
        "",
        "## Conversations with every check right, by topic (all styles)",
        "",
        "| topic | right | of |",
        "|---|---|---|",
    ]
    lines += [f"| {t} | {c.right} | {c.total} |" for t, c in sorted(report.topics.items())]
    if report.judge is not None:
        lines += ["", "## Reply quality, graded by the AI judge (advice only: never part of pass or fail)", ""]
        lines += [
            "| style | judged | failed | " + " | ".join(CRITERIA) + " |",
            "|---|---|---|" + "---|" * len(CRITERIA),
        ]
        for name, stats in report.judge.items():
            means = " | ".join("—" if stats.mean(c) is None else f"{stats.mean(c):.2f}" for c in CRITERIA)
            lines.append(f"| {name} | {stats.judged} | {stats.failed} | {means} |")
    lines += ["", f"## The {len(report.worst)} conversations that went wrong most", ""]
    if not report.worst:
        lines.append("None: every turn matched its gold labels.")
    for row in report.worst:
        lines += [
            f"### {row['id']} ({row['style']}, {row['topic']}): {row['wrong_checks']} wrong check(s)",
            f"- first wrong turn: #{row['first_wrong_turn']}: `{row['said']}`",
            *[f"- {p}" for p in row["problems"]],
            f"- the agent said: {row['reply']}",
            "",
        ]
    return "\n".join(lines) + "\n"


def write_report(report: EvalReport, directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"eval_{report.when.isoformat()}" + ("" if report.mode == "rules" else f"_{report.mode}")
    md, js = directory / f"{stem}.md", directory / f"{stem}.json"
    md.write_text(render(report), encoding="utf-8", newline="\n")
    js.write_text(json.dumps(report.as_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return md, js


def headline(report: EvalReport) -> dict[str, float | None]:
    o = report.overall
    return {"intent": o.intent.rate, "decision": o.decision.rate, "citation": o.citation.rate, "locale": o.locale.rate}


def save_baseline(full: EvalReport, small: EvalReport, path: Path = BASELINE_PATH) -> None:
    """Record what the rules reach today on the whole set and on the 20-conversation sample the test runs."""
    data = {"full": headline(full), "sample": headline(small)}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
