"""Say a few messages to the agent and print every move it made, with the checks that refused it (read-only debugging).

Run from team_b with the same settings as the evaluation (agent_env.sh.example, live mode, a Groq key):

    python ../Docs/output/eval_runs/trace_turns.py "how much do you charge for delivery" --conversation "refund NS-20512" 01012345601
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "src"))

from team_b import eval_conversations as ec  # noqa: E402
from team_b.config import Settings  # noqa: E402
from team_b.container import build_container  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("messages", nargs="+", help="messages said one after the other in ONE chat")
    parser.add_argument("--chat", default="trace-1")
    args = parser.parse_args()
    settings = Settings.from_env().model_copy(update={"fixed_today": ec.EVAL_TODAY, "store": "memory"})
    container = build_container(settings)
    assert container.orchestrator is not None
    container.orchestrator.set_agent(mode="on", move_picker=None)
    for text in args.messages:
        reply = await container.orchestrator.handle_turn(ec.TENANT, args.chat, text)
        trace = await container.traces.get(ec.TENANT, reply.trace_id)
        assert trace is not None
        print(f"\n>>> {text}")
        print(f"    intents={[i.name for i in trace.intents]} decision={reply.decision.value} "
              f"fallback={trace.fallback_reason} escalation={trace.escalation_reason}")
        for step in trace.agent_steps:
            failed = [f"{c.name}: {c.reason}" for c in step.checks if not c.passed]
            move = {k: v for k, v in step.move.items() if k in ("kind", "tool", "query", "intent", "question_key", "slot", "reason")}
            print(f"    - {move} {'REFUSED ' + str(failed) if failed else ''} -> {step.result[:90]!r}")
        print(f"    reply: {reply.text[:160]!r}")


asyncio.run(main())
