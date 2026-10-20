"""Replays the recorded inputs of the writer, reviewer and knowledge jobs to ONE other model and saves its replies.

The inputs come from record_run.py (a normal agent run on the real knowledge service). The same prompts, schema and
temperature 0 go to the model; nothing else changes. Usage (tokens, latency, failures) goes to the usage log.

    python ../docs/reports/eval_runs/models/replay_jobs.py --calls <record_calls.jsonl> --model openai/gpt-oss-20b --slug gpt-oss-20b --jobs writer,reviewer,knowledge
Keys come from GROQ_REPLAY_KEYS (comma separated) in the environment.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "src"))

from agent_service.adapters.llm import OpenAICompatibleLLM  # noqa: E402
from agent_service.adapters.usage_log import UsageLog, tagged  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--calls", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--jobs", default="writer,reviewer,knowledge")
    parser.add_argument("--effort", default="")
    parser.add_argument("--pause", type=float, default=6.0, help="seconds between calls (free-plan token budget)")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    jobs = set(args.jobs.split(","))
    calls = [json.loads(l) for l in args.calls.read_text(encoding="utf-8").splitlines() if l.strip()]
    todo = [(i, c) for i, c in enumerate(calls) if c["job"] in jobs and "reply" in c]
    log_path = os.environ.get("REPLAY_USAGE_LOG", "")
    llm = OpenAICompatibleLLM(
        base_url="https://api.groq.com/openai/v1", model=args.model, api_key=os.environ["GROQ_REPLAY_KEYS"],
        timeout_s=90, reasoning_effort=args.effort or None,
        usage_log=UsageLog(log_path, f"replay_{args.slug}") if log_path else None,
    )
    out = []
    for n, (index, call) in enumerate(todo, 1):
        started = time.time()
        row = {"index": index, "job": call["job"], "model": args.model}
        try:
            with tagged(call["job"]):
                row["reply"] = await llm.complete_json(
                    system=call["system"], user=call["user"], schema_hint=call["schema_hint"], temperature=call["temperature"]
                )
        except Exception as exc:  # a failure is a result: the model could not answer this input
            row["error"] = f"{type(exc).__name__}: {exc}"[:200]
        row["seconds"] = round(time.time() - started, 1)
        out.append(row)
        print(f"{n}/{len(todo)} {call['job']} {'ERROR' if 'error' in row else 'ok'}", flush=True)
        args.out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in out) + "\n", encoding="utf-8")
        await asyncio.sleep(args.pause)
    return 0


raise SystemExit(asyncio.run(main()))
