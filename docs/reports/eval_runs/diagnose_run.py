"""Run conversations and keep EVERYTHING about each turn, so a wrong citation or an unbacked fact can be explained.

The normal evaluation report only counts. This helper runs the same conversations the same way (same container, same
checks, from orchestrator/src/agent_service/eval_conversations.py) and writes one JSON line per turn: what the customer said, the
gold, the decision, the citations, the full reply, the facts the check flagged, the facts the session held, and why
it was marked wrong. Nothing in the product is changed.

Run from the team_b folder (keys and settings come from the environment, like `python -m agent_service eval`):

    python ../docs/reports/eval_runs/diagnose_run.py --set eval/conversations --style ar --topics K,T3 --out out.jsonl
    python ../docs/reports/eval_runs/diagnose_run.py --set eval/conversations --llm --agent-mode on --concurrency 4 ...
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "src"))

from agent_service import eval_conversations as ec  # noqa: E402
from agent_service.adapters.usage_log import current_call  # noqa: E402
from agent_service.config import Settings  # noqa: E402
from agent_service.container import build_container  # noqa: E402


RECORDS: list[dict] = []


class Recorder:
    """--record: keeps every AI call (job, prompts, reply) so the same inputs can be replayed to other models."""

    def __init__(self, inner):  # type: ignore[no-untyped-def]
        self._inner = inner

    async def complete_json(self, *, system, user, schema_hint, temperature=0.0):  # type: ignore[no-untyped-def]
        record = {"job": current_call()[0], "kind": "json", "system": system, "user": user, "schema_hint": schema_hint, "temperature": temperature}
        try:
            record["reply"] = await self._inner.complete_json(system=system, user=user, schema_hint=schema_hint, temperature=temperature)
        except Exception as exc:
            record["error"] = type(exc).__name__
            RECORDS.append(record)
            raise
        RECORDS.append(record)
        return record["reply"]

    async def complete_text(self, *, system, user, temperature=0.0):  # type: ignore[no-untyped-def]
        record = {"job": current_call()[0], "kind": "text", "system": system, "user": user, "temperature": temperature}
        try:
            record["reply"] = await self._inner.complete_text(system=system, user=user, temperature=temperature)
        except Exception as exc:
            record["error"] = type(exc).__name__
            RECORDS.append(record)
            raise
        RECORDS.append(record)
        return record["reply"]

    def __getattr__(self, name):  # type: ignore[no-untyped-def]
        return getattr(self._inner, name)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", type=Path, required=True, help="a folder of .jsonl conversation files")
    parser.add_argument("--llm", action="store_true")
    parser.add_argument("--agent-mode", choices=["on", "off", "shadow"], default=None)
    parser.add_argument("--style", action="append", help="only these language styles (repeat)")
    parser.add_argument("--topics", default="", help="only topics starting with one of these, comma separated")
    parser.add_argument("--limit", type=int, default=0, help="at most this many conversations (0 = all)")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--record", type=Path, default=None, help="also save every AI call here (for replaying to other models)")
    args = parser.parse_args()

    settings = Settings.from_env()
    if args.llm and settings.llm == "none":
        raise SystemExit("--llm needs an AI model (see agent_env.sh.example)")
    if not args.llm:
        settings = settings.model_copy(update={"llm": "none", "llm_rewrite": False, "roles": {}})
    container = build_container(
        settings.model_copy(update={"fixed_today": ec.EVAL_TODAY, "store": "memory"}),
        llm_wrapper=(lambda c: Recorder(c)) if args.record else None,
    )
    if args.agent_mode is not None:
        assert container.orchestrator is not None
        container.orchestrator.set_agent(mode=args.agent_mode, move_picker=None)

    conversations = ec.load_set(args.set)
    prefixes = tuple(t for t in args.topics.split(",") if t)
    if args.style:
        conversations = [c for c in conversations if c.language_style in args.style]
    if prefixes:
        conversations = [c for c in conversations if c.topic.startswith(prefixes)]
    if args.limit:
        conversations = conversations[: args.limit]
    print(f"{len(conversations)} conversations", flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    lock = asyncio.Lock()
    gate = asyncio.Semaphore(max(1, args.concurrency))
    done = 0

    async def one(conversation: ec.EvalConversation, handle) -> None:
        nonlocal done
        async with gate:
            try:
                results = await ec.run_conversation(container, conversation)
            except Exception as exc:  # keep going; the error itself is a finding
                async with lock:
                    handle.write(json.dumps({"conversation": conversation.id, "error": repr(exc)}) + "\n")
                    handle.flush()
                return
            session = await container.sessions.load(ec.TENANT, f"eval-{conversation.id}")
            facts = json.dumps(session.facts, default=str, ensure_ascii=False) if session is not None else ""
            async with lock:
                for turn, got in zip(conversation.turns, results, strict=True):
                    handle.write(
                        json.dumps(
                            {
                                "conversation": conversation.id, "style": conversation.language_style,
                                "topic": conversation.topic, "turn": got.index + 1, "said": turn.say,
                                "gold": turn.gold.model_dump(mode="json"), "intent": got.intent,
                                "decision": got.decision, "escalation": got.escalation, "locale": got.locale,
                                "citations": list(got.citations), "reply": got.text, "unbacked": got.unbacked,
                                "wrong": got.wrong, "fallback": got.fallback, "ai_calls": got.ai_calls,
                                "latency_ms": round(got.latency_ms), "session_facts": facts,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                handle.flush()
                done += 1
                print(f"{done}/{len(conversations)} {conversation.id}", flush=True)

    with args.out.open("w", encoding="utf-8") as handle:
        await asyncio.gather(*(one(c, handle) for c in conversations))
    if args.record:
        args.record.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in RECORDS) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
