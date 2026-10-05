"""Run every scenario in scenarios/<tenant>/ and print one line each, then the counts. Exit 1 if any fails."""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from tests.integration.scenario_format import discover, load_scenario  # noqa: E402
from tests.integration.scenario_runner import ScenarioResult, run_scenario  # noqa: E402

COLUMNS = ("id", "owner", "title", "status", "result", "decision", "escalation")


async def run_all() -> list[ScenarioResult]:
    return [await run_scenario(load_scenario(path), tenant) for tenant, path in discover()]


def row(r: ScenarioResult) -> tuple[str, ...]:
    return (
        r.scenario_id,
        r.owner or "-",
        r.title,
        r.status,
        r.outcome,
        r.final_decision or "-",
        r.escalation_reason or "-",
    )


def render(results: list[ScenarioResult]) -> str:
    rows = [COLUMNS, *(row(r) for r in results)]
    widths = [max(len(r[i]) for r in rows) for i in range(len(COLUMNS))]
    lines = ["  ".join(cell.ljust(w) for cell, w in zip(r, widths, strict=True)).rstrip() for r in rows]
    for r in results:
        lines += [f"  FAIL {f}" for f in r.failures] + (
            [f"  skipped {r.scenario_id}: {r.skip_reason}"] if r.skip_reason else []
        )
    active = sum(r.status == "active" for r in results)
    failing = sum(r.outcome == "failed" for r in results)
    lines.append(f"active: {active}  pending: {len(results) - active}  failing: {failing}")
    return "\n".join(lines)


def main() -> int:
    results = asyncio.run(run_all())
    print(render(results))
    return 1 if any(r.outcome == "failed" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
