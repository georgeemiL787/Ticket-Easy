"""Writer comparison (`eval --writer-compare`): the same Arabic conversations answered by two reply writers.

The Arabic (ar) and Arabizi conversations of the evaluation set run twice on the demo shop: once with the usual writer
model for every reply, once with the Egyptian-Arabic model (job writer_egyptian) writing the Arabic and Arabizi replies.
Both runs pass the same checks and the same reviewer. The replies are saved side by side in
reports/writer_compare_<date>.csv, with the AI judge's grades when --judge is given and an empty rating column for a
person (1 to 5, how natural the Egyptian Arabic is). Nothing here decides pass or fail: it is advice on the model.
"""

import csv
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from agent_service.config import Settings
from agent_service.container import build_container
from agent_service.eval_conversations import EVAL_TODAY, EvalConversation, TurnResult, run_conversation
from agent_service.judge import CRITERIA, Judge, JudgeScore

STYLES = ("ar", "arabizi")
WRITERS = ("default", "egyptian")
ROLE = "writer_egyptian"


def columns() -> list[str]:
    cols = ["conversation", "style", "turn", "customer", "reply_default", "reply_egyptian"]
    cols += [f"judge_{writer}_{name}" for writer in WRITERS for name in CRITERIA]
    return [*cols, "rating_default", "rating_egyptian"]  # the two last columns are for a person: 1 to 5


def without_egyptian(settings: Settings) -> Settings:
    """The same settings with no Egyptian-Arabic model: every reply is written by the usual writer."""
    return settings.model_copy(update={"roles": {k: v for k, v in settings.roles.items() if k != ROLE}})


def has_egyptian(settings: Settings) -> bool:
    own = settings.role(ROLE)
    return bool(own.base_url or own.model)


async def run_with(settings: Settings, conversations: Sequence[EvalConversation]) -> dict[str, list[TurnResult]]:
    container = build_container(settings.model_copy(update={"fixed_today": EVAL_TODAY, "store": "memory"}))
    return {c.id: await run_conversation(container, c) for c in conversations}


async def compare_writers(
    conversations: Sequence[EvalConversation],
    settings: Settings,
    out: Path,
    *,
    judge: Judge | None = None,
    when: date | None = None,
) -> tuple[Path, int]:
    """Run the ar and arabizi conversations with both writers; returns (the CSV path, the number of replies)."""
    chosen = [c for c in conversations if c.language_style in STYLES]
    if not has_egyptian(settings):
        raise ValueError("set TEAM_B_LLM_BASE_URL_WRITER_EGYPTIAN and TEAM_B_LLM_MODEL_WRITER_EGYPTIAN (see README)")
    usual = await run_with(without_egyptian(settings), chosen)
    egyptian = await run_with(settings, chosen)
    path = out / f"writer_compare_{(when or date.today()).isoformat()}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    with path.open("w", encoding="utf-8-sig", newline="") as handle:  # utf-8-sig so Excel reads the Arabic
        writer = csv.DictWriter(handle, fieldnames=columns())
        writer.writeheader()
        for conversation in chosen:
            for first, second in zip(usual[conversation.id], egyptian[conversation.id], strict=True):
                row: dict[str, Any] = {
                    "conversation": conversation.id,
                    "style": conversation.language_style,
                    "turn": first.index + 1,
                    "customer": first.said,
                    "reply_default": first.text,
                    "reply_egyptian": second.text,
                    "rating_default": "",
                    "rating_egyptian": "",
                }
                if judge is not None:
                    for name, result in zip(WRITERS, (first, second), strict=True):
                        score: JudgeScore | None = await judge.score(result.said, result.text, result.style)
                        for criterion in CRITERIA:
                            row[f"judge_{name}_{criterion}"] = score.values()[criterion] if score else ""
                writer.writerow(row)
                rows += 1
    return path, rows
