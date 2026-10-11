"""Check a finished load run on a running server.

    python -m loadtest.check --url http://127.0.0.1:8010 [--csv run_stats.csv]


Reads GET /v1/_test/report (the server needs TEAM_B_ENABLE_TEST_ADMIN=1), prints per-stage p50/p95, the decisions
and the reasons the agent handed off, runs the safety checker and a recovery probe, and prints Locust's p50/p95 per
endpoint from its --csv file when given. Exit code 1 on any problem.
"""

import argparse
import asyncio
import csv
import sys
from pathlib import Path

import httpx

from loadtest.checker import chaos_effects, check, decisions, stage_percentiles
from loadtest.run import TENANT, probe_recovery, table


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--csv", help="Locust's *_stats.csv")
    args = parser.parse_args(argv)
    async with httpx.AsyncClient(base_url=args.url, timeout=120) as http:
        failing = (await http.get("/v1/_test/state")).json()["failing"]
        if failing:
            print("a plug is still failing:", failing, "(wait for it to recover)")
            return 1
        report = (await http.get("/v1/_test/report", params={"tenant_id": TENANT})).json()
        problems = check(report) + await probe_recovery(http)
    if args.csv:
        text = await asyncio.to_thread(Path(args.csv).read_text, encoding="utf-8")
        rows = {
            r["Name"]: {"count": int(r["Request Count"]), "p50_ms": float(r["50%"]), "p95_ms": float(r["95%"])}
            for r in csv.DictReader(text.splitlines())
        }
        table("per endpoint (Locust, ms)", rows)
        slow = [n for n, r in rows.items() if r["p95_ms"] > 3000 and n != "Aggregated"]
        problems += [f"p95 above 3 s on {n}" for n in slow]
    table("per stage (from the traces, ms)", stage_percentiles(report))
    print("\ndecisions:", dict(decisions(report)))
    print("handoff reasons:", dict(chaos_effects(report)))
    print("\nCHECKER:", "PASS" if not problems else f"{len(problems)} problem(s)")
    for line in problems[:20]:
        print("  -", line)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
