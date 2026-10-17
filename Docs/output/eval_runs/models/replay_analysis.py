"""Scores the replays of the writer, reviewer and knowledge jobs (run from the repository root).

Baselines are the replies recorded in the original run (writer and knowledge: qwen3.8-27b; reviewer: gpt-oss-120b).
No metric here is ground truth: "agreement" means agreeing with the baseline model, and "grounded" uses the product's own
check_grounded (no number, date, id or currency in the reply that its input does not contain). The writer's wording
quality is not scored by a program: see writer_replies_to_grade.csv, a blinded sheet for a person.
"""
import csv
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, "team_b/src")
from team_b.brain.composer import check_grounded  # noqa: E402

RUNS = Path("Docs/output/eval_runs")
BASELINE = {"writer": "qwen/qwen3.8-27b", "knowledge": "qwen/qwen3.8-27b", "reviewer": "openai/gpt-oss-120b"}
ARABIC = re.compile(r"[؀-ۿ]")
calls = [json.loads(l) for l in (RUNS / "models/replay_inputs.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]

replies = defaultdict(dict)  # (job, model) -> index -> reply or None
for i, c in enumerate(calls):
    if c["job"] in BASELINE and "reply" in c:
        replies[(c["job"], BASELINE[c["job"]])][i] = c["reply"]
for f in sorted((RUNS / "models").glob("replay_*.jsonl")):
    if f.name == "replay_inputs.jsonl":
        continue
    for line in f.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            replies[(r["job"], r["model"])][r["index"]] = r.get("reply")  # None when the model could not answer

usage = defaultdict(lambda: defaultdict(list))
for f in (RUNS / "usage").glob("replay_*.jsonl"):
    for line in f.read_text(encoding="utf-8").splitlines():
        if line.strip():
            u = json.loads(line)
            usage[u["model"]][u["job"]].append(u)

rows = []
for (job, model), by_index in sorted(replies.items()):
    base = replies[(job, BASELINE[job])]
    shared = [i for i in by_index if i in base]
    answered = [i for i in shared if by_index[i] is not None]
    row = {"job": job, "model": model, "is_baseline": model == BASELINE[job], "inputs": len(shared), "answered": len(answered), "failed": len(shared) - len(answered)}
    if job == "writer":
        texts = {i: str((by_index[i] or {}).get("text", "")) for i in answered}
        clean = [i for i in answered if texts[i].strip() and not check_grounded(texts[i], [calls[i]["user"]])]
        row.update({
            "grounded_pct": round(100 * len(clean) / len(shared), 1) if shared else None,
            "empty_replies": sum(1 for i in answered if not texts[i].strip()),
            "same_script_as_baseline_pct": round(100 * sum(bool(ARABIC.search(texts[i])) == bool(ARABIC.search(str(base[i].get("text", "")))) for i in answered) / len(answered), 1) if answered else None,
            "avg_reply_chars": round(sum(len(texts[i]) for i in answered) / len(answered)) if answered else None,
        })
    elif job == "reviewer":
        ok = {i: bool(by_index[i].get("ok")) for i in answered}
        row.update({
            "says_ok_pct": round(100 * sum(ok.values()) / len(answered), 1) if answered else None,
            "agrees_with_baseline_pct": round(100 * sum(ok[i] == bool(base[i].get("ok")) for i in answered) / len(answered), 1) if answered else None,
            "looser_than_baseline": sum(ok[i] and not base[i].get("ok") for i in answered),
            "stricter_than_baseline": sum((not ok[i]) and bool(base[i].get("ok")) for i in answered),
            "baseline_blocked_inputs": sum(not base[i].get("ok") for i in shared),
        })
    elif job == "knowledge":
        def cites(r): return tuple(sorted(r.get("citations") or []))
        def nums(r): return tuple(sorted(re.findall(r"\d+(?:[.,]\d+)?", str(r.get("answer", "")))))
        row.update({
            "answerable_agrees_pct": round(100 * sum(bool(by_index[i].get("answerable")) == bool(base[i].get("answerable")) for i in answered) / len(answered), 1) if answered else None,
            "same_citations_pct": round(100 * sum(cites(by_index[i]) == cites(base[i]) for i in answered) / len(answered), 1) if answered else None,
            "same_numbers_pct": round(100 * sum(nums(by_index[i]) == nums(base[i]) for i in answered) / len(answered), 1) if answered else None,
            "grounded_pct": round(100 * sum(not check_grounded(str(by_index[i].get("answer", "")), [calls[i]["user"]]) for i in answered) / len(shared), 1) if shared else None,
        })
    u = usage.get(model, {}).get(job, [])
    ok_calls = [x for x in u if x["status"] == "ok"]
    if ok_calls:
        row["avg_tokens_per_call"] = round(sum((x["input_tokens"] or 0) + (x["output_tokens"] or 0) for x in ok_calls) / len(ok_calls))
        row["latency_p50_ms"] = sorted(x["latency_ms"] for x in ok_calls)[len(ok_calls) // 2]
    row["note"] = "baseline: the recorded reply" if model == BASELINE[job] else "replayed on the Groq free tier"
    rows.append(row)

fields = list(dict.fromkeys(k for r in rows for k in r))
with (RUNS / "models/replay_comparison.csv").open("w", encoding="utf-8", newline="") as h:
    w = csv.DictWriter(h, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)

# blinded writer sheet for a person: same input, every model's wording, shuffled order, models hidden
rng = random.Random(20261017)
sheet = []
models = [m for (j, m) in replies if j == "writer"]
for i in sorted(replies[("writer", BASELINE["writer"])]):
    options = [(m, str((replies[("writer", m)].get(i) or {}).get("text", ""))) for m in models if replies[("writer", m)].get(i)]
    rng.shuffle(options)
    sheet.append({"input_number": i, "what_the_writer_was_given": calls[i]["user"][:700].replace("\n", " | "),
                  **{f"reply_{chr(65 + k)}": text for k, (_, text) in enumerate(options)},
                  **{f"score_{chr(65 + k)}_1_to_5": "" for k in range(len(options))},
                  "_key_which_model_is_A_B_C": " / ".join(m for m, _ in options)})
if sheet:
    with (RUNS / "models/writer_replies_to_grade.csv").open("w", encoding="utf-8-sig", newline="") as h:
        w = csv.DictWriter(h, fieldnames=list(dict.fromkeys(k for r in sheet for k in r)))
        w.writeheader()
        w.writerows(sheet)
for r in rows:
    print({k: v for k, v in r.items() if k not in ("note",)})
