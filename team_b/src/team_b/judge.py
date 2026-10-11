"""An optional AI judge of how natural and clear the agent's replies read (prompts/judge_v1.md).

It scores clarity, politeness, register match and helpfulness from 1 to 5, with a one-line reason each. It is advice for
the person reading the evaluation report: it never takes part in a pass or fail decision. To keep the judge honest, ten
percent of the judged replies are written to a CSV for a person to grade, and `agreement` compares the two.
"""

import csv
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ConfigDict, Field

from team_b.brain.llm_nlu import PromptTemplate
from team_b.brain.redaction import redact
from team_b.domain.base import FrozenModel
from team_b.observability import get_logger
from team_b.ports import LLMClient

log = get_logger(__name__)
JUDGE_PROMPT = "judge_v1"
CRITERIA = ("clarity", "politeness", "register", "helpfulness")
SPOTCHECK_SHARE = 0.10
STYLE_WORDS = {
    "en": "English",
    "ar": "Egyptian Arabic",
    "mixed": "Arabic mixed with English (the reply should be Egyptian Arabic)",
    "arabizi": "Arabizi (Egyptian Arabic in Latin letters and digits)",
}
SCHEMA_HINT = {
    "clarity": "1-5",
    "politeness": "1-5",
    "register": "1-5",
    "helpfulness": "1-5",
    "reasons": {"clarity": "one line"},
}


class JudgeScore(FrozenModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    clarity: int
    politeness: int
    register_: int = Field(alias="register")  # the grade for "register" (the plain name is taken by pydantic)
    helpfulness: int
    reasons: dict[str, str] = Field(default_factory=dict)

    def values(self) -> dict[str, int]:
        return {name: getattr(self, "register_" if name == "register" else name) for name in CRITERIA}


def parse_score(data: dict[str, Any]) -> JudgeScore | None:
    """The model's answer as a score, or None when any grade is missing, not a whole number, or outside 1 to 5."""
    grades: dict[str, int] = {}
    for name in CRITERIA:
        value = data.get(name)
        if isinstance(value, bool) or not isinstance(value, int | float) or value != int(value) or not 1 <= value <= 5:
            return None
        grades[name] = int(value)
    raw = data.get("reasons")
    reasons = {k: str(v).strip()[:200] for k, v in raw.items() if k in CRITERIA} if isinstance(raw, dict) else {}
    return JudgeScore(**grades, reasons=reasons)


class Judge:
    def __init__(self, llm: LLMClient, prompt: PromptTemplate | None = None) -> None:
        self._llm = llm
        self._prompt = prompt or PromptTemplate.load(JUDGE_PROMPT)

    @property
    def prompt_version(self) -> str:
        return self._prompt.version

    async def score(self, customer: str, reply: str, style: str) -> JudgeScore | None:
        """The judge's grades for one reply, or None when the model failed or answered badly (counted, never raised)."""
        words = STYLE_WORDS.get(style, style)
        user = self._prompt.render_user(style=words, customer=redact(customer), reply=redact(reply))
        system = self._prompt.system.replace("{{style}}", words)
        try:
            data = await self._llm.complete_json(system=system, user=user, schema_hint=SCHEMA_HINT)
        except Exception as exc:  # a judge that fails must never stop an evaluation
            log.warning("judge_failed", reason=type(exc).__name__)
            return None
        return parse_score(data)


# ---- what the report shows ----


@dataclass
class JudgedTurn:
    conversation: str
    style: str
    index: int
    customer: str
    reply: str
    score: JudgeScore


@dataclass
class JudgeStats:
    judged: int = 0
    failed: int = 0
    totals: dict[str, int] = field(default_factory=lambda: dict.fromkeys(CRITERIA, 0))

    def add(self, score: JudgeScore | None) -> None:
        if score is None:
            self.failed += 1
            return
        self.judged += 1
        for name, value in score.values().items():
            self.totals[name] += value

    def mean(self, name: str) -> float | None:
        return self.totals[name] / self.judged if self.judged else None

    def as_dict(self) -> dict[str, Any]:
        return {"judged": self.judged, "failed": self.failed, **{f"mean_{n}": self.mean(n) for n in CRITERIA}}


def pick_spotcheck(turns: Sequence[JudgedTurn], share: float = SPOTCHECK_SHARE, seed: int = 7) -> list[JudgedTurn]:
    """About `share` of the judged turns (at least one), chosen at random but the same way every run."""
    if not turns:
        return []
    count = max(1, round(len(turns) * share))
    chosen = random.Random(seed).sample(range(len(turns)), min(count, len(turns)))
    return [turns[i] for i in sorted(chosen)]


SPOTCHECK_COLUMNS = [
    "conversation", "turn", "style", "customer", "reply",
    *[f"judge_{c}" for c in CRITERIA], *[f"reason_{c}" for c in CRITERIA], *[f"human_{c}" for c in CRITERIA],
]  # fmt: skip


def write_spotcheck(turns: Sequence[JudgedTurn], path: Path) -> int:
    """The turns for a person to grade: the judge's grades are filled in, the human_* columns are left empty."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:  # utf-8-sig so Excel reads the Arabic
        writer = csv.DictWriter(handle, fieldnames=SPOTCHECK_COLUMNS)
        writer.writeheader()
        for t in turns:
            row: dict[str, Any] = {
                "conversation": t.conversation,
                "turn": t.index + 1,
                "style": t.style,
                "customer": t.customer,
                "reply": t.reply,
            }
            for name in CRITERIA:
                row[f"judge_{name}"] = t.score.values()[name]
                row[f"reason_{name}"] = t.score.reasons.get(name, "")
                row[f"human_{name}"] = ""
            writer.writerow(row)
    return len(turns)


@dataclass
class Agreement:
    graded: int  # rows a person has graded (all four human grades filled in)
    per_criterion: dict[str, dict[str, float]]  # exact, within_one, mean_abs_diff
    overall_within_one: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "graded": self.graded,
            "per_criterion": self.per_criterion,
            "overall_within_one": self.overall_within_one,
        }


def agreement(path: Path) -> Agreement:
    """How close the judge is to the person who graded the spot-check file: exact, within one point, mean difference."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    graded = [r for r in rows if all((r.get(f"human_{c}") or "").strip() for c in CRITERIA)]
    per: dict[str, dict[str, float]] = {}
    close = total = 0
    for name in CRITERIA:
        diffs = [abs(int(r[f"judge_{name}"]) - int(r[f"human_{name}"].strip())) for r in graded]
        if diffs:
            per[name] = {
                "exact": sum(d == 0 for d in diffs) / len(diffs),
                "within_one": sum(d <= 1 for d in diffs) / len(diffs),
                "mean_abs_diff": sum(diffs) / len(diffs),
            }
            close += sum(d <= 1 for d in diffs)
            total += len(diffs)
    return Agreement(len(graded), per, close / total if total else None)
