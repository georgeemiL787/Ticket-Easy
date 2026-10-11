"""Fill a database with a few hundred synthetic conversations so the dashboard has something to show.

Usage: python scripts/seed_dashboard.py [--db var/demo.sqlite3] [--conversations 300] [--days 14] [--seed 1] [--reset]

The conversations run through the real brain and the stand-ins (policy answers, unknown questions, handoffs that staff
claim and resolve, risky messages, a search outage, ...), spread over the last --days days. Then look at them with:

    TEAM_B_STORE=sqlite TEAM_B_DB_PATH=var/demo.sqlite3 make run      # http://127.0.0.1:8010/dashboard
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from team_b.config import Settings  # noqa: E402
from team_b.container import build_container  # noqa: E402
from team_b.demo_seed import clock_for, seed  # noqa: E402
from team_b.observability import configure_logging  # noqa: E402


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=ROOT / "var" / "demo.sqlite3")
    parser.add_argument("--conversations", type=int, default=300)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--reset", action="store_true", help="delete the database file first")
    args = parser.parse_args(argv)
    if args.reset and args.db.exists():
        args.db.unlink()
    configure_logging(json_logs=False, level=logging.WARNING)  # one line per turn would drown the summary
    clock = clock_for(args.days)
    container = build_container(Settings(store="sqlite", db_path=args.db), clock=clock)
    report = await seed(container, clock, conversations=args.conversations, days=args.days, seed=args.seed)
    print(f"seeded {report.conversations} conversations ({report.turns} turns) into {args.db}")
    print(f"cases opened: {report.cases}, resolved by staff: {report.resolved_cases}")
    print("kinds:", ", ".join(f"{k}={v}" for k, v in sorted(report.kinds.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
