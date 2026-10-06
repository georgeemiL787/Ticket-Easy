"""Command line: python -m team_b eval-nlu [--mode rules|llm|both] | seed-demo [--conversations N] [--days N]"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

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


async def seed_demo(conversations: int, days: int, force: bool) -> int:
    """Fill the SQLite database with synthetic conversations for the dashboard, unless it already has some."""
    import logging

    from team_b.container import build_container
    from team_b.demo_seed import TENANT, clock_for, seed
    from team_b.observability import configure_logging

    settings = Settings.from_env().model_copy(update={"store": "sqlite"})
    configure_logging(json_logs=False, level=logging.WARNING)
    clock = clock_for(days)
    container = build_container(settings, clock=clock)
    if not force and await container.traces.query(TENANT, limit=1):
        print(f"{settings.db_path} already has conversations: nothing seeded (use --force to add more)")
        return 0
    report = await seed(container, clock, conversations=conversations, days=days)
    print(f"seeded {report.conversations} conversations ({report.turns} turns), {report.cases} cases")
    print(f"database: {settings.db_path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m team_b", description="Ticket-Easy Team B tools")
    commands = parser.add_subparsers(dest="command", required=True)
    nlu = commands.add_parser("eval-nlu", help="measure understanding accuracy on the labelled messages")
    nlu.add_argument("--mode", choices=["rules", "llm", "both"], default="rules")
    nlu.add_argument("--tenant", default="shop_001")
    nlu.add_argument("--data", type=Path, default=LABELLED_PATH, help="labelled messages (JSON lines)")
    nlu.add_argument("--save-baseline", action="store_true", help="record the intent accuracy as the new baseline")
    seed = commands.add_parser("seed-demo", help="fill the database with synthetic conversations for the dashboard")
    seed.add_argument("--conversations", type=int, default=150)
    seed.add_argument("--days", type=int, default=14)
    seed.add_argument("--force", action="store_true", help="add conversations even if the database already has some")
    args = parser.parse_args(argv)
    if args.command == "seed-demo":
        return asyncio.run(seed_demo(args.conversations, args.days, args.force))
    asyncio.run(eval_nlu(args.mode, args.tenant, args.data, args.save_baseline))
    return 0


if __name__ == "__main__":
    sys.exit(main())
