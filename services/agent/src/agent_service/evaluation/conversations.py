"""Measure the whole agent on short conversations, separately for English, Egyptian Arabic, mixed and Arabizi.

Each conversation in eval/conversations/*.jsonl is run turn by turn through the real core (stand-ins for the shop and
the policy search) and every turn is compared with its gold labels:

- intent accuracy: the first intent understood equals the first gold intent ("none" when the gold list is empty);
- decision accuracy: the decision (answer, clarify, handoff, ...) is the gold one, or one of several allowed ones;
- citation accuracy: an answer cites at least one of the passages the gold allows;
- locale match: the reply is in the style of the customer (English, Arabic or Arabizi);
- handoff precision and recall: handoffs made versus handoffs the gold wanted;
- entity recall: the gold details (order id) were read;
- latency p50 and p95 per style, and the conversations that went wrong most, with their first wrong turn.

Measures for comparing two ways of running the agent (rules or AI, agent mode on or off; see measures_of):
solved without a person, needless handoffs, missed escalations, wrong or unapproved actions, unbacked facts (must be 0),
AI calls per message, time per reply. They are only added to the report: how a turn is judged right or wrong is the
five checks above. `--label NAME` saves a run as reports/eval_NAME_<date>.json/.md and `eval-compare A B` shows two runs
side by side.

The gold labels come from the policies and business rules, not from the core (see scripts/make_eval_set.py).
"""

import json
import re
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

from pydantic import Field

from agent_service.adapters.llm_limits import RateBudgetExceeded
from agent_service.config import PROJECT_ROOT, Settings
from agent_service.container import Container, build_container
from agent_service.domain.agent import AgentMode
from agent_service.domain.base import FrozenModel
from agent_service.domain.understanding import Locale
from agent_service.evaluation.judge import CRITERIA, Judge, JudgedTurn, JudgeScore, JudgeStats
from agent_service.monitoring.metrics import percentile
from agent_service.ports import LLMClient, MovePicker
from agent_service.replies.composer import check_grounded, default_composer
from agent_service.replies.smalltalk import TODAY_WORDS
from agent_service.turn.shadow import Agreement, compare
from agent_service.understanding.choices import mask_order_id, short_date

CONVERSATIONS_DIR = PROJECT_ROOT / "eval" / "conversations"
BASELINE_PATH = PROJECT_ROOT / "eval" / "eval_baseline.json"
TENANT = "shop_001"
EVAL_TODAY = date(2026, 9, 28)  # the demo shop's orders are dated against this day
STYLES = ("en", "ar", "mixed", "arabizi")
LOCALE_OF_STYLE = {"en": "en", "ar": "ar", "mixed": "ar", "arabizi": "arabizi"}  # mixed customers get Egyptian Arabic
WORST = 10
MAX_DISAGREEMENTS = 40
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
    """A small, even sample: `per_style` conversations of every style, each of a different topic, the topics spread
    over the whole list (policy questions, order requests, hand-overs, the agent's own topics)."""
    topics = sorted({c.topic or "-" for c in conversations}, key=lambda t: (len(t), t))
    chosen: list[EvalConversation] = []
    for number, style in enumerate(STYLES):
        rows = [c for c in conversations if c.language_style == style]
        used: set[str] = set()
        for k in range(per_style):
            wanted = topics[((k * len(topics)) // per_style + number) % len(topics)]
            pick = next((c for c in rows if (c.topic or "-") == wanted and c.id not in used), None)
            pick = pick or next((c for c in rows if c.id not in used), None)
            if pick is not None:
                used.add(pick.id)
                chosen.append(pick)
    return chosen


# ---- running ----


class CountingLLM:
    """The AI model, with a count of how many times it was asked (the evaluation's "AI calls per message")."""

    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner
        self.calls = 0
        self.budget_hit = False  # the provider's per-minute budget ran out during a call

    async def complete_json(
        self, *, system: str, user: str, schema_hint: dict[str, Any], temperature: float = 0.0
    ) -> dict[str, Any]:
        self.calls += 1
        try:
            return await self._inner.complete_json(
                system=system, user=user, schema_hint=schema_hint, temperature=temperature
            )
        except RateBudgetExceeded:
            self.budget_hit = True
            raise

    async def complete_text(self, *, system: str, user: str, temperature: float = 0.0) -> str:
        self.calls += 1
        return await self._inner.complete_text(system=system, user=user, temperature=temperature)  # type: ignore[attr-defined,no-any-return]


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
    ai_calls: int = 0  # times the AI model was asked while answering this message
    unbacked: list[str] = field(default_factory=list)  # facts in the reply that nothing the agent knew contains
    shadow: Agreement | None = None  # shadow mode: does the agent's first move agree with what was done?
    fallback: str | None = None  # agent mode: why the loop gave up and the usual flow answered this message


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


def fallback_kind(reason: str) -> str:
    """The category of an agent fallback text (the same ones the dashboard uses)."""
    lowered = reason.lower()
    if "longer than" in lowered:
        return "too_slow"
    if "move picker" in lowered:
        return "model_error"
    if "refused" in lowered:
        return "refused_moves"
    return "too_many_moves" if "moves without" in lowered else "other"


def decisions_allowed(gold: Gold) -> tuple[str, ...]:
    if gold.decision is None:
        return ()
    return (gold.decision,) if isinstance(gold.decision, str) else gold.decision


def wants_a_person(gold: Gold) -> bool:
    """The gold says this turn must reach a person: an escalation reason, or a handoff as the only decision."""
    return gold.escalation is not None or decisions_allowed(gold) == ("handoff",)


def accepts_handoff(gold: Gold) -> bool:
    return wants_a_person(gold) or "handoff" in decisions_allowed(gold)


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
ORDER_ID = re.compile(r"NS-\d{4,6}")


def known_text(container: Container) -> str:
    """Everything the agent may legitimately say a fact from, besides the conversation itself: its reply templates and
    the shop's and the policies' data (dates also written the short way, as the order list shows them)."""
    folder = container.settings.fixtures_dir / TENANT
    data = "\n".join(
        (folder / name).read_text(encoding="utf-8-sig") for name in ("backend.json", "policies.json", "rules.json")
    )
    short = [short_date(d, locale) for d in set(_ISO_DATE.findall(data)) for locale in Locale]
    return "\n".join([*default_composer().template_texts(), data, *short])


async def unbacked_facts(
    container: Container, chat_id: str, said: Sequence[str], text: str, known: str, citations: Sequence[str] = ()
) -> list[str]:
    """Facts in a reply that none of the customer's messages, the agent's known texts or the order facts contain.

    check_grounded is the very check that stops the AI from inventing facts; run again here on every reply that was
    really sent, so a gap in the live check would show up as a number above zero. Like the live check, it lets
    "today" pass in a chat with no facts in it ("how can I help you today?" promises nothing): no citation and no
    fact held by the session. Any other time word ("tomorrow"), and "today" next to a fact, is still flagged."""
    session = await container.sessions.load(TENANT, chat_id)
    extra = [json.dumps(session.facts, default=str, ensure_ascii=False)] if session is not None else []
    case = [session.handoff_case_id] if session is not None and session.handoff_case_id else []
    # a reference the shop really issued (RET-30001, CAN-30001): its own audit log is the proof, so "Your reference
    # number is CAN-30001" is a fact the agent was told; one the shop never issued is still unbacked
    issued = [e.reference_id for e in container.shop.audit_log(TENANT) if e.reference_id] if container.shop else []
    # a passage the reply carries in its own citation list was retrieved by the search (code refuses any other)
    sources = [known, *said, *extra, *case, *issued, *citations]
    # the order list shows ids hidden except their last digits (NS-**877): that is the same id, not a new number
    masked = [mask_order_id(i) for i in dict.fromkeys(ORDER_ID.findall("\n".join(sources)))]
    found = check_grounded(text, [*sources, *masked])
    if not citations and not (session is not None and session.facts):
        found = [p for p in found if p.removeprefix("new time word ") not in TODAY_WORDS]
    return found


async def run_conversation(
    container: Container, conversation: EvalConversation, ai_calls: Callable[[], int] | None = None
) -> list[TurnResult]:
    assert container.orchestrator is not None
    results: list[TurnResult] = []
    chat_id = f"eval-{conversation.id}"
    known = known_text(container)
    said: list[str] = []
    tenant = container.tenants.get(TENANT)
    action_tools = {name: spec.action_tool for name, spec in tenant.intents.items() if spec.action_tool}
    for index, turn in enumerate(conversation.turns):
        calls_before = ai_calls() if ai_calls is not None else 0
        started = time.perf_counter()
        reply = await container.orchestrator.handle_turn(TENANT, chat_id, turn.say)
        latency = (time.perf_counter() - started) * 1000
        said.append(turn.say)
        trace = await container.traces.get(TENANT, reply.trace_id)
        assert trace is not None
        escalation = trace.escalation_reason.value if trace.escalation_reason else None
        result = TurnResult(
            conversation=conversation.id, style=conversation.language_style, index=index, said=turn.say,
            decision=reply.decision.value, escalation=escalation,
            locale=reply.locale.value, citations=tuple(reply.citations),
            intent=trace.intents[0].name if trace.intents else "none", entities=dict(trace.entities), text=reply.text,
            latency_ms=latency, ai_calls=(ai_calls() - calls_before) if ai_calls is not None else 0,
            fallback=trace.fallback_reason,
        )  # fmt: skip
        result.unbacked = await unbacked_facts(container, chat_id, said, reply.text, known, reply.citations)
        result.shadow = compare(trace, action_tools)
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
    # the comparison measures (see measures())
    solved: int = 0  # conversations the agent finished with no handoff, where the gold wants no person
    no_person_needed: int = 0  # conversations whose gold wants no person at any turn
    needless_handoffs: int = 0  # turns that handed over where the gold has an opinion and wants no handoff
    handoff_opinions: int = 0  # turns where the gold says whether a handoff is wanted
    missed_escalations: int = 0  # turns where the gold wants a person and none was offered
    escalations_wanted: int = 0
    wrong_actions: int = 0  # writes the gold did not expect, or sent without a rule check
    writes: int = 0
    unbacked_replies: int = 0  # replies holding a fact nothing the agent knew contains (must be 0)
    fallbacks: int = 0  # agent mode: messages where the agent loop gave up and the usual flow answered
    fallback_reasons: dict[str, int] = field(default_factory=dict)
    ai_calls: int = 0
    shadow_asked: int = 0  # shadow mode: turns where the agent's first move was recorded
    shadow_agreed: int = 0  # ... and agreed with what the usual flow did

    def add_conversation(
        self, conversation: EvalConversation, results: Sequence[TurnResult], audit: Sequence[Any]
    ) -> None:
        """The measures that need the whole conversation and the shop's audit log."""
        wants_person = any(wants_a_person(t.gold) for t in conversation.turns)
        handed_over = any(r.decision == "handoff" for r in results)
        self.no_person_needed += int(not wants_person)
        self.solved += int(not wants_person and not handed_over)
        expected_writes = sum(1 for t in conversation.turns if "execute" in decisions_allowed(t.gold))
        self.writes += len(audit)
        self.wrong_actions += max(0, len(audit) - expected_writes) + sum(1 for e in audit if not e.policy_request_id)
        for turn, got in zip(conversation.turns, results, strict=True):
            self.ai_calls += got.ai_calls
            if got.shadow is not None:
                self.shadow_asked += 1
                self.shadow_agreed += int(got.shadow.agrees)
            self.unbacked_replies += int(bool(got.unbacked))
            if got.fallback:
                self.fallbacks += 1
                kind = fallback_kind(got.fallback)
                self.fallback_reasons[kind] = self.fallback_reasons.get(kind, 0) + 1
            wanted, allowed = wants_a_person(turn.gold), decisions_allowed(turn.gold)
            if wanted or allowed:
                self.handoff_opinions += 1
            self.escalations_wanted += int(wanted)
            self.missed_escalations += int(wanted and got.decision != "handoff")
            self.needless_handoffs += int(
                got.decision == "handoff"
                and (allowed or turn.gold.escalation is not None)
                and not accepts_handoff(turn.gold)
            )

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

    def measures(self) -> dict[str, float | int | None]:
        """What the comparison between two runs looks at (all counted from this run, none from the gold alone)."""
        return {
            "solved_without_person": self.solved,
            "solved_without_person_rate": self.solved / self.conversations if self.conversations else None,
            "no_person_needed": self.no_person_needed,
            "needless_handoffs": self.needless_handoffs,
            "missed_escalations": self.missed_escalations,
            "escalations_wanted": self.escalations_wanted,
            "wrong_or_unapproved_actions": self.wrong_actions,
            "writes": self.writes,
            "unbacked_facts": self.unbacked_replies,
            "agent_fallbacks": self.fallbacks,
            "ai_calls_per_message": self.ai_calls / self.turns if self.turns else None,
            "time_p50_ms": percentile(self.latencies, 0.5),
            "time_p95_ms": percentile(self.latencies, 0.95),
            "shadow_asked": self.shadow_asked,
            "shadow_agreement": self.shadow_agreed / self.shadow_asked if self.shadow_asked else None,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "style": self.style, "conversations": self.conversations, "turns": self.turns,
            "measures": self.measures(), "agent_fallback_reasons": dict(self.fallback_reasons),
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
    label: str = ""  # a name for this run (--label), used in the report's file name
    agent_mode: str | None = None  # the agent mode this run used (on, off, shadow), if it was set
    shadow_disagreements: list[dict[str, Any]] = field(default_factory=list)  # shadow mode: where the agent differs
    complete: bool = (
        True  # False: the run stopped (--batch, or the provider's budget ran out) before the last conversation
    )
    remaining: int = 0  # conversations still to run

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode, "date": self.when.isoformat(), "label": self.label, "agent_mode": self.agent_mode,
            "shadow_disagreements": self.shadow_disagreements,
            "topics": {t: {"right": c.right, "total": c.total} for t, c in sorted(self.topics.items())},
            "judge": None if self.judge is None else {s: j.as_dict() for s, j in self.judge.items()},
            "overall": self.overall.as_dict(), "styles": {s: r.as_dict() for s, r in self.styles.items()},
            "worst_conversations": self.worst,
        }  # fmt: skip


def save_checkpoint(
    path: Path, conversation_id: str, results: list[TurnResult], writes: list[bool], scores: list[Any]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "id": conversation_id,
        "results": [asdict(r) for r in results],
        "writes": writes,
        "scores": [None if sc is None else sc.model_dump(by_alias=True) for sc in scores],
    }
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_checkpoint(path: Path) -> dict[str, tuple[list[TurnResult], list[bool], list[Any]]]:
    """The conversations a stopped run finished: id -> (results, write flags, judge scores)."""
    found: dict[str, tuple[list[TurnResult], list[bool], list[Any]]] = {}
    if not path.is_file():
        return found
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        results = []
        for r in row["results"]:
            shadow = r.pop("shadow", None)
            results.append(TurnResult(**r, shadow=Agreement(**shadow) if shadow else None))
        scores = [None if sc is None else JudgeScore.model_validate(sc) for sc in row["scores"]]
        found[row["id"]] = (results, row["writes"], scores)
    return found


async def evaluate(
    conversations: Sequence[EvalConversation],
    settings: Settings,
    *,
    mode: str = "rules",
    when: date | None = None,
    judge: Judge | None = None,
    label: str = "",
    agent_mode: AgentMode | None = None,
    move_picker: MovePicker | None = None,
    checkpoint: Path | None = None,
    batch: int | None = None,
) -> EvalReport:
    """Run the conversations on one container (each in its own chat) and measure every turn.

    checkpoint: a file that keeps every finished conversation (one JSON line each). Conversations already in it are
    not run again, so a long run can be stopped and continued (`eval --resume`). batch: run at most this many new
    conversations, then stop. A conversation during which the provider's budget ran out is dropped, not recorded
    (its answers came from the fallback), and the run stops there: report.complete is False until all are done."""
    counter: list[CountingLLM] = []

    def count(llm: LLMClient) -> LLMClient:
        counter.append(CountingLLM(llm))
        return counter[-1]

    container = build_container(
        settings.model_copy(update={"fixed_today": EVAL_TODAY, "store": "memory"}), llm_wrapper=count
    )
    assert container.shop is not None and container.orchestrator is not None
    if agent_mode is not None or move_picker is not None:
        container.orchestrator.set_agent(mode=agent_mode, move_picker=move_picker)
    disagreements: list[dict[str, Any]] = []
    kinds = {t.name: t.operation_kind for t in await container.capabilities.list_tools(TENANT)}
    styles = {s: StyleReport(s) for s in STYLES}
    overall = StyleReport("all")
    scored: list[tuple[int, str, dict[str, Any]]] = []
    topics: dict[str, Counter] = defaultdict(Counter)
    judge_stats = {s: JudgeStats() for s in (*STYLES, "all")} if judge is not None else None
    judged: list[JudgedTurn] = []
    saved = load_checkpoint(checkpoint) if checkpoint is not None else {}
    done = 0
    complete = True

    def absorb(
        conversation: EvalConversation, results: list[TurnResult], writes: list[bool], scores: list[Any]
    ) -> None:
        """Count one finished conversation into the report (writes: was each write the shop saw rule-checked)."""
        locale = LOCALE_OF_STYLE[conversation.language_style]
        report = styles[conversation.language_style]
        report.conversations += 1
        overall.conversations += 1
        for turn, got in zip(conversation.turns, results, strict=True):
            report.add(turn.gold, got, locale)
            overall.add(turn.gold, got, locale)
        for turn, got in zip(conversation.turns, results, strict=True):
            if got.shadow is not None and not got.shadow.agrees and len(disagreements) < MAX_DISAGREEMENTS:
                disagreements.append(
                    {
                        "conversation": conversation.id, "turn": got.index + 1, "style": conversation.language_style,
                        "said": turn.say, "today": got.shadow.today, "agent": got.shadow.agent, "why": got.shadow.why,
                    }
                )  # fmt: skip
        audit = [SimpleNamespace(policy_request_id="checked" if ok else "") for ok in writes]
        report.add_conversation(conversation, results, audit)
        overall.add_conversation(conversation, results, audit)
        if judge_stats is not None:
            for turn, got, score in zip(conversation.turns, results, scores, strict=True):
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

    for conversation in conversations:
        if conversation.id in saved:  # finished in an earlier run
            old_results, old_writes, old_scores = saved[conversation.id]
            absorb(conversation, old_results, old_writes, old_scores)
            continue
        if batch is not None and done >= batch:
            complete = False
            break
        for c in counter:
            c.budget_hit = False
        audit_before = len(container.shop.audit_log(TENANT))
        results = await run_conversation(
            container, conversation, (lambda: sum(c.calls for c in counter)) if counter else None
        )
        if any(c.budget_hit for c in counter):  # the answers came from the fallback: not a measurement of the model
            complete = False
            break
        writes = [
            bool(e.policy_request_id)
            for e in container.shop.audit_log(TENANT)[audit_before:]
            if kinds.get(e.tool, "write") != "read"
        ]
        scores = []
        if judge is not None:
            scores = [
                await judge.score(t.say, r.text, conversation.language_style)
                for t, r in zip(conversation.turns, results, strict=True)
            ]
        absorb(conversation, results, writes, scores)
        done += 1
        if checkpoint is not None:
            save_checkpoint(checkpoint, conversation.id, results, writes, scores)
    scored.sort(key=lambda item: (-item[0], item[1]))
    final = EvalReport(mode, when or date.today(), styles, overall, [row for _, _, row in scored[:WORST]], dict(topics))
    final.judge, final.judged, final.label = judge_stats, judged, label
    final.agent_mode, final.shadow_disagreements = agent_mode, disagreements
    final.complete = complete
    final.remaining = len(conversations) - sum(r.conversations for r in styles.values())
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
    lines += [
        "",
        "## Measures for comparing runs (counted from this run; the gold only says what should happen)",
        "",
        "| style | solved without a person | needless handoffs | missed escalations | wrong or unapproved actions "
        "| unbacked facts | AI calls per message | time p50 ms | time p95 ms |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        m = r.measures()
        calls = m["ai_calls_per_message"]
        solved = (
            f"{m['solved_without_person']} of {m['no_person_needed']} ({pct(m['solved_without_person_rate'])} of all)"
        )
        missed = f"{m['missed_escalations']} of {m['escalations_wanted']}"
        wrong = f"{m['wrong_or_unapproved_actions']} of {m['writes']} writes"
        lines.append(
            f"| {r.style} | {solved} | {m['needless_handoffs']} | {missed} | {wrong} | {m['unbacked_facts']} "
            f"| {'—' if calls is None else f'{calls:.2f}'} | {ms(m['time_p50_ms'])} | {ms(m['time_p95_ms'])} |"
        )
    lines += [
        "",
        "- solved without a person: conversations that ended with no handoff, out of those whose gold wants no person;",
        "- needless handoff: a turn that handed over where the gold wants none; missed escalation: the gold wants a",
        "  person and none was offered; wrong or unapproved action: a write the gold did not expect, or one sent",
        "  without a rule check; unbacked fact: a sent reply with a number, date, id or link nothing the agent knew",
        "  contains (it must be 0).",
    ]
    if report.overall.shadow_asked:
        o = report.overall
        lines += [
            "",
            "## Shadow mode: does the agent's first move agree with what the usual flow did?",
            "",
            f"The agent was asked on {o.shadow_asked} turns and agreed on {o.shadow_agreed} "
            f"({pct(o.shadow_agreed / o.shadow_asked)}). Nothing it proposed was worked: the usual flow answered.",
            "",
        ]
        if report.shadow_disagreements:
            lines += [
                "| conversation | turn | the customer said | the usual flow | the agent | why |",
                "|---|---|---|---|---|---|",
            ]
            lines += [
                f"| {d['conversation']} | {d['turn']} | {d['said']} | {d['today']} | {d['agent']} | {d['why']} |"
                for d in report.shadow_disagreements
            ]
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
    if report.label:
        stem = f"eval_{report.label}_{report.when.isoformat()}"
    else:
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
