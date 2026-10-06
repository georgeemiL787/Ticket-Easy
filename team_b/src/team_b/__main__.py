"""Command line: python -m team_b eval-nlu [--mode rules|llm|both] | eval --set eval/conversations [--llm]"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from team_b import eval_conversations as convo
from team_b.brain.llm_nlu import LLMNLU
from team_b.brain.nlu import NLU, RuleBasedNLU
from team_b.config import Settings
from team_b.container import build_llm
from team_b.domain.tenant import TenantRegistry
from team_b.nlu_eval import LABELLED_PATH, EvalReport, evaluate, load_labelled, render, save_baseline


def build_nlu(mode: str, settings: Settings) -> NLU:
    """The understanding to measure: the rules alone, or the AI-assisted one when an AI model is configured."""
    if mode == "rules":
        return RuleBasedNLU()
    llm = build_llm(settings)
    if llm is None:
        raise SystemExit("--mode llm needs an AI model: set TEAM_B_LLM=ollama or openrouter (see .env.example).")
    return LLMNLU(llm)


async def eval_nlu(mode: str, tenant_id: str, data: Path, save: bool) -> list[EvalReport]:
    settings = Settings.from_env()
    tenant = TenantRegistry.from_dir(settings.config_dir).get(tenant_id)
    messages = load_labelled(data)
    reports = []
    for one in ("rules", "llm") if mode == "both" else (mode,):
        report = await evaluate(build_nlu(one, settings), tenant, messages, mode=one)
        reports.append(report)
        print(render(report))
        print()
        if save:
            save_baseline(report)
    return reports


async def eval_conversations(directory: Path, use_llm: bool, out: Path, save: bool, tenant_check: bool = True) -> int:
    settings = Settings.from_env()
    if use_llm and settings.llm == "none":
        raise SystemExit("--llm needs an AI model: set TEAM_B_LLM=ollama or openrouter (see .env.example).")
    if not use_llm:
        settings = settings.model_copy(update={"llm": "none", "llm_rewrite": False})
    conversations = convo.load_set(directory)
    mode = "llm" if use_llm else "rules"
    report = await convo.evaluate(conversations, settings, mode=mode)
    print(convo.render(report))
    markdown, data = convo.write_report(report, out)
    print(f"wrote {markdown} and {data}")
    if save:
        if use_llm:
            raise SystemExit("the baseline is recorded from the rules (run without --llm)")
        small = await convo.evaluate(convo.sample(conversations, 5), settings, mode=mode)
        convo.save_baseline(report, small)
        print(f"recorded the baseline in {convo.BASELINE_PATH}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m team_b", description="Ticket-Easy Team B tools")
    commands = parser.add_subparsers(dest="command", required=True)
    nlu = commands.add_parser("eval-nlu", help="measure understanding accuracy on the labelled messages")
    nlu.add_argument("--mode", choices=["rules", "llm", "both"], default="rules")
    nlu.add_argument("--tenant", default="shop_001")
    nlu.add_argument("--data", type=Path, default=LABELLED_PATH, help="labelled messages (JSON lines)")
    nlu.add_argument("--save-baseline", action="store_true", help="record the intent accuracy as the new baseline")
    full = commands.add_parser("eval", help="measure the whole agent on the conversation set, per language style")
    full.add_argument("--set", type=Path, default=convo.CONVERSATIONS_DIR, help="folder of conversations (JSON lines)")
    full.add_argument("--llm", action="store_true", help="use the configured AI model for understanding")
    full.add_argument("--out", type=Path, default=Path("reports"), help="where the report files go")
    full.add_argument("--save-baseline", action="store_true", help="record the rules' result as the baseline")
    args = parser.parse_args(argv)
    if args.command == "eval":
        return asyncio.run(eval_conversations(args.set, args.llm, args.out, args.save_baseline))
    asyncio.run(eval_nlu(args.mode, args.tenant, args.data, args.save_baseline))
    return 0


if __name__ == "__main__":
    sys.exit(main())
