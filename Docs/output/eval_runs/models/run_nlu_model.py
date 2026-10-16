"""Model comparison helper: the 155 labelled messages through the AI reader of ONE model, with a per-message record.

Same code and prompt as `python -m team_b eval-nlu --mode llm` (nlu_eval.evaluate with brain.llm_nlu.LLMNLU on the reader
job); it only adds a file with one line per message (expected first intent, the first intent got, whether the AI really
answered or the rules answered instead, "rules_fallback"). The model is the reader job's model (TEAM_B_LLM_MODEL_READER).
Run from team_b:

    python ../Docs/output/eval_runs/models/run_nlu_model.py --out ../Docs/output/eval_runs/models/nlu_<slug>.jsonl
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "src"))

from team_b.adapters.llm_registry import LLMRegistry  # noqa: E402
from team_b.brain.llm_nlu import LLMNLU  # noqa: E402
from team_b.config import Settings  # noqa: E402
from team_b.container import build_container  # noqa: E402
from team_b.nlu_eval import LABELLED_PATH, evaluate, load_labelled, render  # noqa: E402


class Recorder:
    """Wraps the reader and remembers, for each message, what it answered and by which method."""

    def __init__(self, inner: LLMNLU) -> None:
        self.inner = inner
        self.rows: list[dict] = []

    async def understand(self, text, session, tenant):  # type: ignore[no-untyped-def]
        result = await self.inner.understand(text, session, tenant)
        self.rows.append({"text": text, "method": result.method, "first": result.intents[0].name if result.intents else None})
        return result


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=LABELLED_PATH)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--tenant", default="shop_001")
    args = parser.parse_args()
    settings = Settings.from_env()
    llm = LLMRegistry(settings).llm_for("reader")
    if llm is None:
        raise SystemExit("the reader job has no AI model: set the TEAM_B_LLM_* variables")
    tenant = build_container(settings).tenants.get(args.tenant)
    messages = load_labelled(args.data)
    recorder = Recorder(LLMNLU(llm))
    report = await evaluate(recorder, tenant, messages, mode="llm")
    print(render(report))
    with args.out.open("w", encoding="utf-8") as handle:
        for message, row in zip(messages, recorder.rows, strict=True):
            expected = message.intents[0] if message.intents else None
            handle.write(
                json.dumps(
                    {"id": message.id, "language": message.language, "expected": expected, "got": row["first"],
                     "method": row["method"], "correct": row["first"] == expected},
                    ensure_ascii=False,
                )
                + "\n"
            )
    return 0


raise SystemExit(asyncio.run(main()))
