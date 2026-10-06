"""Load and chaos run without Locust: N customers at once, optional failures, then the checker. Works on the app in the
same process (default) or on a running server (--url, which must run with TEAM_B_ENABLE_TEST_ADMIN=1).

    python -m loadtest.run --customers 50 --conversations 6 --chaos rule_checker:30@5 --chaos shop:30@15

--chaos PLUG:SECONDS@START  switches the stand-in off START seconds into the run for SECONDS seconds (it heals itself).
Prints p50/p95 per endpoint and per stage, what the agent did when parts failed, and the checker's verdict.
Exit code 1 when the checker finds a problem, a recovery probe fails, or --p95-limit is exceeded.
"""

import argparse
import asyncio
import logging
import random
import sys
import tempfile
import time
from contextlib import AsyncExitStack
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from loadtest import demo
from loadtest.checker import chaos_effects, check, decisions, percentile, stage_percentiles

TENANT = demo.TENANT


async def play(http: httpx.AsyncClient, conversation_id: str, messages: tuple[str, ...], timings: list[float]) -> int:
    """Send the messages one after another. Returns how many answers were not 200 (a 429 counts)."""
    bad = 0
    for text in messages:
        started = time.perf_counter()
        response = await http.post(
            f"/v1/conversations/{conversation_id}/messages", json={"tenant_id": TENANT, "text": text}
        )
        timings.append((time.perf_counter() - started) * 1000)
        bad += response.status_code != 200
    return bad


async def customer(http: httpx.AsyncClient, number: int, conversations: int, seed: int, timings: list[float]) -> int:
    rng = random.Random(f"{seed}-{number}")
    bad = 0
    for i in range(conversations):
        chosen = demo.pick(rng)
        bad += await play(http, f"load-{seed}-{number}-{i}", chosen.messages, timings)
        await asyncio.sleep(rng.uniform(0, 0.05))
    return bad


async def chaos_plan(http: httpx.AsyncClient, plan: list[tuple[str, float, float]], started: float) -> None:
    async def one(plug: str, seconds: float, start: float) -> None:
        await asyncio.sleep(max(0.0, start - (time.perf_counter() - started)))
        response = await http.post("/v1/_test/chaos", json={"plug": plug, "seconds": seconds})
        response.raise_for_status()

    await asyncio.gather(*(one(*item) for item in plan))


async def probe_recovery(http: httpx.AsyncClient) -> list[str]:
    """After the chaos: ordinary requests work again, with no restart."""
    problems: list[str] = []
    answer = await http.post(
        "/v1/conversations/probe-1/messages", json={"tenant_id": TENANT, "text": "What is your return policy?"}
    )
    if answer.status_code != 200 or answer.json()["decision"] != "answer":
        problems.append(f"recovery: a policy question was not answered ({answer.status_code} {answer.text[:80]})")
    status = await http.post(
        "/v1/conversations/probe-2/messages",
        json={"tenant_id": TENANT, "text": "Where is my order NS-20877? My phone is 01012345601"},
    )
    if status.status_code != 200 or status.json()["decision"] != "answer":
        problems.append("recovery: an order lookup was not answered")
    for step, text in enumerate(("I want to return order NS-20790, the size is wrong", "01123456702")):
        reply = await http.post("/v1/conversations/probe-3/messages", json={"tenant_id": TENANT, "text": text})
        if step == 1 and reply.json()["decision"] not in ("confirm", "refuse"):
            problems.append(f"recovery: an action was not checked and confirmed ({reply.json()['decision']})")
    return problems


def table(title: str, rows: dict[str, dict[str, Any]]) -> None:
    print(f"\n{title}")
    print(f"  {'name':<28}{'count':>8}{'p50 ms':>10}{'p95 ms':>10}")
    for name, row in rows.items():
        p50, p95 = row["p50_ms"], row["p95_ms"]
        print(f"  {name:<28}{row['count']:>8}{p50 or 0:>10.1f}{p95 or 0:>10.1f}")


def parse_chaos(items: list[str]) -> list[tuple[str, float, float]]:
    plan = []
    for item in items:
        plug, _, rest = item.partition(":")
        seconds, _, start = rest.partition("@")
        plan.append((plug, float(seconds), float(start or 0)))
    return plan


async def run(args: argparse.Namespace, http: httpx.AsyncClient) -> int:
    timings: list[float] = []
    started = time.perf_counter()
    plan = parse_chaos(args.chaos)
    chaos = asyncio.create_task(chaos_plan(http, plan, started)) if plan else None
    bad = await asyncio.gather(
        *(customer(http, n, args.conversations, args.seed, timings) for n in range(args.customers))
    )
    elapsed = time.perf_counter() - started
    if chaos is not None:
        await chaos
    if plan:  # let the last failure heal by itself: that is what is being tested
        waited = max(0.0, max(s + st for _, s, st in plan) - elapsed) + 0.2
        await asyncio.sleep(waited)
        state = (await http.get("/v1/_test/state")).json()
        if state["failing"]:
            print("still failing after the plan:", state["failing"])
            return 1
    report = (await http.get("/v1/_test/report", params={"tenant_id": TENANT})).json()
    problems = check(report) + await probe_recovery(http)

    rate = len(timings) / elapsed
    print(f"{args.customers} customers, {len(timings)} messages in {elapsed:.1f}s ({rate:.0f} messages/s)")
    table(
        "per endpoint (client side, ms)",
        {
            "POST /v1/conversations/{id}/messages": {
                "count": len(timings),
                "p50_ms": percentile(timings, 0.5),
                "p95_ms": percentile(timings, 0.95),
            }
        },
    )
    table("per stage (from the traces, ms)", stage_percentiles(report))
    print("\ndecisions:", dict(decisions(report)))
    print("handoff reasons:", dict(chaos_effects(report)))
    print(f"answers that were not 200 (rate limit): {sum(bad)}")
    p95 = percentile(timings, 0.95) or 0.0
    if args.p95_limit and p95 > args.p95_limit:
        problems.append(f"p95 {p95:.0f} ms is above the limit {args.p95_limit:.0f} ms")
    print("\nCHECKER:", "PASS" if not problems else f"{len(problems)} problem(s)")
    for line in problems[:20]:
        print("  -", line)
    return 1 if problems else 0


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--customers", type=int, default=50)
    parser.add_argument("--conversations", type=int, default=5, help="conversations per customer")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--chaos", action="append", default=[], help="PLUG:SECONDS@START, repeatable")
    parser.add_argument("--p95-limit", type=float, default=3000.0, help="fail above this many ms (0: no limit)")
    parser.add_argument("--url", help="a running server (needs TEAM_B_ENABLE_TEST_ADMIN=1); default: in this process")
    args = parser.parse_args(argv)

    async with AsyncExitStack() as stack:
        if args.url:
            http = await stack.enter_async_context(httpx.AsyncClient(base_url=args.url, timeout=60))
        else:
            import structlog

            from team_b.api.app import create_app
            from team_b.config import Settings

            tmp = stack.enter_context(tempfile.TemporaryDirectory())
            settings = Settings(
                store="memory", db_path=Path(tmp) / "load.sqlite3", llm="none", enable_test_admin=True,
                rate_limit_per_minute=100000, fixed_today=date(2026, 9, 28),  # the demo data is dated against this day
            )  # fmt: skip
            app = create_app(settings=settings)
            await stack.enter_async_context(app.router.lifespan_context(app))
            structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))  # quiet the run
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            http = await stack.enter_async_context(
                httpx.AsyncClient(transport=transport, base_url="http://load", timeout=60)
            )
        return await run(args, http)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
