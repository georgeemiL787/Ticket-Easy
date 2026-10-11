"""Measure understanding accuracy against hand-labelled messages (eval/nlu_labelled.jsonl).

Metrics:
- intent accuracy: the first intent found equals the first intent of the label (both "none" counts as right);
- intent list exact match: the whole ordered list is right; shown again for messages with two or more intents;
- entity precision and recall for order_id, phone and amount (a value must match exactly);
- language accuracy, and accuracy of "wants a person";
- the worst misses, for finding what the word lists lack.
"""

import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import Field

from team_b.brain.nlu import NLU
from team_b.config import PROJECT_ROOT
from team_b.domain.base import FrozenModel
from team_b.domain.tenant import TenantConfig
from team_b.domain.understanding import Language, NLUResult

LABELLED_PATH = PROJECT_ROOT / "eval" / "nlu_labelled.jsonl"
BASELINE_PATH = PROJECT_ROOT / "eval" / "nlu_baseline.json"
ENTITY_TYPES = ("order_id", "phone", "amount")
STYLES = (Language.EN, Language.AR, Language.MIXED, Language.ARABIZI)
WORST_MISSES = 10


class LabelledMessage(FrozenModel):
    id: str = Field(min_length=1)
    text: str
    language: Language
    intents: tuple[str, ...] = ()
    entities: dict[str, str] = Field(default_factory=dict)
    wants_human: bool = False


def load_labelled(path: Path = LABELLED_PATH) -> list[LabelledMessage]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [LabelledMessage.model_validate(json.loads(line)) for line in lines]


@dataclass
class Miss:
    message: LabelledMessage
    predicted: NLUResult
    score: int
    problems: list[str] = field(default_factory=list)


@dataclass
class Rate:
    right: int = 0
    total: int = 0

    def add(self, ok: bool) -> None:
        self.right += ok
        self.total += 1

    @property
    def value(self) -> float:
        return self.right / self.total if self.total else 0.0


@dataclass
class EntityStats:
    matched: int = 0
    predicted: int = 0
    gold: int = 0

    @property
    def precision(self) -> float | None:
        return self.matched / self.predicted if self.predicted else None

    @property
    def recall(self) -> float | None:
        return self.matched / self.gold if self.gold else None


@dataclass
class EvalReport:
    mode: str
    count: int = 0
    intent: Rate = field(default_factory=Rate)
    intent_by_style: dict[Language, Rate] = field(default_factory=lambda: defaultdict(Rate))
    exact: Rate = field(default_factory=Rate)
    multi_exact: Rate = field(default_factory=Rate)
    language: Rate = field(default_factory=Rate)
    language_by_style: dict[Language, Rate] = field(default_factory=lambda: defaultdict(Rate))
    human: Rate = field(default_factory=Rate)
    entities: dict[str, EntityStats] = field(default_factory=lambda: {t: EntityStats() for t in ENTITY_TYPES})
    misses: list[Miss] = field(default_factory=list)


def _first(intents: Sequence[str]) -> str | None:
    return intents[0] if intents else None


def _entity_errors(message: LabelledMessage, result: NLUResult, report: EvalReport) -> list[str]:
    problems = []
    for kind in ENTITY_TYPES:
        gold, found = message.entities.get(kind), result.entities.get(kind)
        stats = report.entities[kind]
        stats.gold += gold is not None
        stats.predicted += found is not None
        if gold is not None and gold == found:
            stats.matched += 1
        elif gold is not None or found is not None:
            problems.append(f"{kind}: expected {gold!r}, got {found!r}")
    return problems


def score_message(message: LabelledMessage, result: NLUResult, report: EvalReport) -> None:
    gold = list(message.intents)
    found = [i.name for i in result.intents]
    primary_ok = _first(gold) == _first(found)
    report.count += 1
    report.intent.add(primary_ok)
    report.intent_by_style[message.language].add(primary_ok)
    report.exact.add(gold == found)
    if len(gold) >= 2:
        report.multi_exact.add(gold == found)
    language_ok = result.language is message.language
    report.language.add(language_ok)
    report.language_by_style[message.language].add(language_ok)
    human_ok = result.wants_human == message.wants_human
    report.human.add(human_ok)

    problems = _entity_errors(message, result, report)
    if gold != found:
        problems.insert(0, f"intents: expected {gold}, got {found}")
    if not language_ok:
        problems.append(f"language: expected {message.language.value}, got {result.language.value}")
    if not human_ok:
        problems.append(f"wants_human: expected {message.wants_human}, got {result.wants_human}")
    if problems:
        severity = 3 * (not primary_ok) + 2 * (gold != found) + len(problems)
        report.misses.append(Miss(message, result, severity, problems))


async def evaluate(
    nlu: NLU, tenant: TenantConfig, messages: Sequence[LabelledMessage], mode: str = "rules"
) -> EvalReport:
    report = EvalReport(mode=mode)
    for message in messages:
        score_message(message, await nlu.understand(message.text, None, tenant), report)
    report.misses.sort(key=lambda m: (-m.score, m.message.id))
    return report


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def _rate_line(label: str, rate: Rate) -> str:
    return f"{label:<32} {_pct(rate.value)}  ({rate.right}/{rate.total})"


def render(report: EvalReport) -> str:
    lines = [
        f"== NLU evaluation, mode: {report.mode}, {report.count} messages ==",
        _rate_line("intent accuracy (first intent)", report.intent),
        _rate_line("intent list exact match", report.exact),
        _rate_line("multi-intent exact match", report.multi_exact),
        _rate_line("language accuracy", report.language),
        _rate_line("wants-a-person accuracy", report.human),
        "",
        "by style:        intent    language",
    ]
    for style in STYLES:
        intent, language = report.intent_by_style[style], report.language_by_style[style]
        lines.append(f"  {style.value:<10}  {_pct(intent.value):>8}  {_pct(language.value):>8}   (n={intent.total})")
    lines += ["", "entities:        precision   recall"]
    for kind in ENTITY_TYPES:
        stats = report.entities[kind]
        lines.append(f"  {kind:<10}  {_pct(stats.precision):>9}  {_pct(stats.recall):>7}   (gold={stats.gold})")
    shown = min(WORST_MISSES, len(report.misses))
    lines += ["", f"{shown} worst misses (of {len(report.misses)} messages with any error):"]
    for miss in report.misses[:WORST_MISSES]:
        lines.append(f"  [{miss.message.id}] {miss.message.text}")
        lines += [f"      {problem}" for problem in miss.problems]
    return "\n".join(lines)


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, float]:
    """Recorded intent accuracy per mode, e.g. {"rules": 0.82}."""
    if not path.is_file():
        return {}
    return {
        mode: float(entry["intent_accuracy"]) for mode, entry in json.loads(path.read_text(encoding="utf-8")).items()
    }


def save_baseline(report: EvalReport, path: Path = BASELINE_PATH) -> None:
    existing = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    existing[report.mode] = {"intent_accuracy": round(report.intent.value, 4), "messages": report.count}
    path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
