"""Records every AI call of a normal agent run (job, prompts, schema, the model's reply) so the SAME inputs can be replayed to
other models for the other jobs (writer, reviewer, knowledge, summary). Test conversations only (synthetic customers).

Run from team_b like diagnose_run.py; AGENT_USAGE_LOG must be set (it makes each call carry its job name):

    python ../docs/reports/eval_runs/models/record_run.py --set eval/conversations --style en --topics K,T17,T22 --out <calls.jsonl>
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path.cwd() / "src"))

from agent_service.evaluation import conversations as ec  # noqa: E402
from agent_service.adapters.usage_log import current_call  # noqa: E402
from agent_service.config import Settings  # noqa: E402
from agent_service.container import build_container  # noqa: E402

RECORDS: list[dict[str, Any]] = []


class Recorder:
    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def complete_json(self, *, system: str, user: str, schema_hint: dict[str, Any], temperature: float = 0.0) -> dict[str, Any]:
        record = {"job": current_call()[0], "kind": "json", "system": system, "user": user, "schema_hint": schema_hint, "temperature": temperature}
        try:
            reply = await self._inner.complete_json(system=system, user=user, schema_hint=schema_hint, temperature=temperature)
        except Exception as exc:
            record["error"] = type(exc).__name__
            RECORDS.append(record)
            raise
        record["reply"] = reply
        RECORDS.append(record)
        return reply

    async def complete_text(self, *, system: str, user: str, temperature: float = 0.0) -> str:
        record = {"job": current_call()[0], "kind": "text", "system": system, "user": user, "temperature": temperature}
        try:
            reply = await self._inner.complete_text(system=system, user=user, temperature=temperature)
        except Exception as exc:
            record["error"] = type(exc).__name__
            RECORDS.append(record)
            raise
        record["reply"] = reply
        RECORDS.append(record)
        return reply

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", type=Path, required=True)
    parser.add_argument("--style", action="append")
    parser.add_argument("--topics", default="")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings.from_env()
    container = build_container(
        settings.model_copy(update={"fixed_today": ec.EVAL_TODAY, "store": "memory"}), llm_wrapper=lambda c: Recorder(c)
    )
    assert container.orchestrator is not None
    container.orchestrator.set_agent(mode="on", move_picker=None)
    conversations = ec.load_set(args.set)
    prefixes = tuple(t for t in args.topics.split(",") if t)
    if args.style:
        conversations = [c for c in conversations if c.language_style in args.style]
    if prefixes:
        conversations = [c for c in conversations if c.topic.startswith(prefixes)]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    for index, conversation in enumerate(conversations, 1):
        await ec.run_conversation(container, conversation)
        print(f"{index}/{len(conversations)} {conversation.id} calls recorded {len(RECORDS)}", flush=True)
        args.out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in RECORDS) + "\n", encoding="utf-8")
    return 0


raise SystemExit(asyncio.run(main()))
