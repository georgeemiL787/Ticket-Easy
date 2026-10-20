"""Totals from one or more token usage logs (JSONL, one line per AI request; the format is in
docs/reports/eval_runs/USAGE_LOG_FORMAT.md and is the same in the conversation, knowledge and tool services).

    python scripts/usage_summary.py logs/a.jsonl logs/b.jsonl [--csv out.csv]

One row per service, run label, job and model: calls, ok calls, input, output and total tokens, the average total tokens
per ok call, and the latency p50/p95 of the ok calls (milliseconds). A missing token count is not counted as zero: it
is left out of the sums. Standard library only.
"""

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

COLUMNS = (
    "service", "run_label", "job", "model", "calls", "ok_calls", "estimated_calls",
    "input_tokens", "output_tokens", "total_tokens", "avg_tokens_per_ok_call", "latency_p50_ms", "latency_p95_ms",
)  # fmt: skip


def read_lines(paths: Iterable[Path]) -> Iterator[dict[str, Any]]:
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for number, raw in enumerate(handle, 1):
                if not raw.strip():
                    continue
                try:
                    line = json.loads(raw)
                except json.JSONDecodeError:
                    print(f"{path}:{number}: not JSON, skipped", file=sys.stderr)
                    continue
                if isinstance(line, dict):
                    yield line


def percentile(values: list[int], q: float) -> int | None:
    """Nearest-rank percentile (q in 0..100) of a list, or None for an empty one."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q / 100 * len(ordered)) - 1)]


def _num(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def summarise(lines: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for line in lines:
        key = tuple(str(line.get(f) or "") for f in ("service", "run_label", "job", "model"))
        groups[key].append(line)  # type: ignore[index]
    rows: list[dict[str, Any]] = []
    for (service, label, job, model), items in sorted(groups.items()):
        ok = [i for i in items if i.get("status") == "ok"]
        tokens_in = sum(_num(i.get("input_tokens")) or 0 for i in items)
        tokens_out = sum(_num(i.get("output_tokens")) or 0 for i in items)
        counted = [(_num(i.get("input_tokens")) or 0) + (_num(i.get("output_tokens")) or 0) for i in ok]
        latencies = [v for i in ok if (v := _num(i.get("latency_ms"))) is not None]
        rows.append({
            "service": service, "run_label": label, "job": job, "model": model,
            "calls": len(items), "ok_calls": len(ok),
            "estimated_calls": sum(1 for i in items if i.get("estimated") is True),
            "input_tokens": tokens_in, "output_tokens": tokens_out, "total_tokens": tokens_in + tokens_out,
            "avg_tokens_per_ok_call": round(sum(counted) / len(ok), 1) if ok else None,
            "latency_p50_ms": percentile(latencies, 50), "latency_p95_ms": percentile(latencies, 95),
        })  # fmt: skip
    return rows


def render(rows: list[dict[str, Any]]) -> str:
    cells = [[("" if row[c] is None else str(row[c])) for c in COLUMNS] for row in rows]
    widths = [max(len(c), *(len(r[i]) for r in cells)) if cells else len(c) for i, c in enumerate(COLUMNS)]
    out = ["  ".join(c.ljust(w) for c, w in zip(COLUMNS, widths, strict=True))]
    out += ["  ".join(v.ljust(w) for v, w in zip(r, widths, strict=True)) for r in cells]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Totals from token usage logs.")
    parser.add_argument("logs", nargs="+", type=Path, help="one or more usage JSONL files")
    parser.add_argument("--csv", type=Path, help="also write the table to this CSV file")
    args = parser.parse_args(argv)
    rows = summarise(read_lines(args.logs))
    print(render(rows) if rows else "no usage lines found")
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
