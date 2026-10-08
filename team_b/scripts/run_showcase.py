"""Play the long example conversations (eval/showcase/*.json) through the real brain and write a transcript.

Usage: python scripts/run_showcase.py [name ...]      names: english egyptian_arabic mixed arabizi (default: all)

Reads team_b/.env (the AI model, Team A and the flags come from there) but keeps everything in memory. The customer
turns are scripted; every agent reply is produced by the system, so the transcript shows what it really says.
The transcript goes to reports/showcase_<date>.md.
"""

import asyncio
import json
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def load_env() -> None:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())
    os.environ["TEAM_B_STORE"] = "memory"
    os.environ["TEAM_B_LOG_JSON"] = "true"


async def main(names: list[str]) -> int:
    load_env()
    from team_b.config import Settings
    from team_b.container import build_container
    from team_b.observability import configure_logging

    settings = Settings.from_env()
    configure_logging(json_logs=True, level=logging.ERROR)
    out = [f"# Showcase transcript ({date.today()})\n", f"AI model: {settings.llm} / {settings.llm_model}; "
           f"rewrite={settings.llm_rewrite} policy_agent={settings.llm_policy} knowledge={settings.llm_knowledge}; mode={settings.mode}\n"]
    files = sorted((ROOT / "eval" / "showcase").glob("*.json"))
    for path in files:
        if names and path.stem not in names:
            continue
        conv = json.loads(path.read_text(encoding="utf-8"))
        container = build_container(settings)
        orch = container.orchestrator
        assert orch is not None
        out.append(f"\n## {conv['title']}\nCustomer: {conv['customer']}\n")
        for i, text in enumerate(conv["turns"], 1):
            started = time.perf_counter()
            reply = await orch.handle_turn(conv["tenant_id"], f"showcase-{path.stem}", text)
            secs = time.perf_counter() - started
            out.append(f"**{i}. Customer:** {text}\n\n**Agent** _({reply.decision.value}, {secs:.1f}s)_: {reply.text}\n")
            print(f"[{path.stem}] turn {i}/{len(conv['turns'])} {reply.decision.value} {secs:.1f}s", file=sys.stderr)
    report = ROOT / "reports" / f"showcase_{date.today()}.md"
    report.parent.mkdir(exist_ok=True)
    report.write_text("\n".join(out), encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
