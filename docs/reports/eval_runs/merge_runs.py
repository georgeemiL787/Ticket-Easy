"""Merge conversation evaluation reports (standard library; run with python -I).

The report JSON files hold per-style rates and counts but not the per-turn results. The fixed gold files do say, for every
turn, which checks apply (intent, decision, citation, entities, handoff), so the exact denominators are rebuilt from them
and every right-count is recovered as rate x denominator. A report is refused when a recovered count is not a whole
number or does not reproduce the reported rate: nothing is ever averaged from percentages.

    python -I merge_runs.py --reports a.json b.json c.json --styles en,ar,mixed --out merged.json
    python -I merge_runs.py --check a.json        # only verify that the counts of one report can be rebuilt

Limits (printed in the output): a combined latency percentile cannot be rebuilt (the raw latencies are not stored), so
per-style latencies are kept and the combined ones are left empty. Entity recall is merged the same way as the others.
"""

import argparse
import json
import sys
from pathlib import Path

SETS = Path(__file__).resolve().parents[3] / "orchestrator" / "eval" / "conversations"


def decisions(gold):
    d = gold.get("decision")
    return () if d is None else ((d,) if isinstance(d, str) else tuple(d))


def denominators(style):
    """What the gold file says about one language style: how many turns each check applies to."""
    out = dict(turns=0, intent=0, decision=0, citation=0, entities=0, handoff_wanted=0)
    for line in (SETS / f"{style}.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        for turn in json.loads(line)["turns"]:
            gold = turn.get("gold", {})
            out["turns"] += 1
            out["intent"] += gold.get("intents") is not None
            allowed = decisions(gold)
            out["decision"] += bool(allowed)
            out["handoff_wanted"] += "handoff" in allowed
            out["citation"] += bool(gold.get("citations_any"))
            out["entities"] += sum(1 for _ in (gold.get("entities") or {}))
    return out


def whole(value, what):
    if abs(value - round(value)) > 1e-6:
        raise SystemExit(f"cannot rebuild {what}: {value} is not a whole number (the report and the gold disagree)")
    return int(round(value))


def counts(entry, style, label):
    """Exact counts of one style entry of a report, checked against the reported rates."""
    den = denominators(style)
    if entry["turns"] != den["turns"]:
        raise SystemExit(f"{label}: {style} has {entry['turns']} turns but the gold set has {den['turns']} (a sample, not the whole set)")
    m = entry["measures"]
    c = {"conversations": entry["conversations"], "turns": entry["turns"]}
    for key, report_key in (("intent", "intent_accuracy"), ("decision", "decision_accuracy"), ("citation", "citation_accuracy"), ("entities", "entity_recall")):
        rate = entry.get(report_key)
        c[f"{key}_total"] = den[key]
        c[f"{key}_right"] = whole(rate * den[key], f"{label} {style} {key}") if rate is not None and den[key] else 0
    c["locale_total"], c["locale_right"] = den["turns"], whole(entry["locale_match"] * den["turns"], f"{label} {style} locale")
    c["handoff_wanted"] = den["handoff_wanted"]
    c["handoff_right"] = whole((entry["handoff_recall"] or 0) * den["handoff_wanted"], f"{label} {style} handoff recall")
    prec = entry.get("handoff_precision")
    c["handoff_made"] = whole(c["handoff_right"] / prec, f"{label} {style} handoff precision") if prec else 0
    if c["handoff_made"] and abs(c["handoff_right"] / c["handoff_made"] - prec) > 1e-9:
        raise SystemExit(f"{label} {style}: handoff precision does not reproduce")
    for key in ("solved_without_person", "no_person_needed", "needless_handoffs", "missed_escalations", "escalations_wanted", "wrong_or_unapproved_actions", "writes", "unbacked_facts", "agent_fallbacks"):
        c[key] = m[key]
    c["ai_calls"] = whole((m["ai_calls_per_message"] or 0) * entry["turns"], f"{label} {style} AI calls")
    c["latency_p50_ms"], c["latency_p95_ms"] = entry.get("latency_p50_ms"), entry.get("latency_p95_ms")
    c["fallback_reasons"] = entry.get("agent_fallback_reasons", {})
    return c


def pct(right, total):
    return round(100 * right / total, 1) if total else None


def combine(per_style):
    total = {k: sum(c[k] for c in per_style.values()) for k in per_style[next(iter(per_style))] if isinstance(per_style[next(iter(per_style))][k], int)}
    out = {"styles": sorted(per_style), "conversations": total["conversations"], "turns": total["turns"]}
    for key in ("intent", "decision", "citation", "entities", "locale"):
        out[f"{key}_pct"] = pct(total[f"{key}_right"], total[f"{key}_total"])
        out[f"{key}_right"], out[f"{key}_total"] = total[f"{key}_right"], total[f"{key}_total"]
    out["handoff_precision_pct"] = pct(total["handoff_right"], total["handoff_made"])
    out["handoff_recall_pct"] = pct(total["handoff_right"], total["handoff_wanted"])
    out["solved_without_person"], out["no_person_needed"] = total["solved_without_person"], total["no_person_needed"]
    out["solved_without_person_pct"] = pct(total["solved_without_person"], total["no_person_needed"])
    for key in ("needless_handoffs", "missed_escalations", "escalations_wanted", "wrong_or_unapproved_actions", "writes", "unbacked_facts", "agent_fallbacks", "ai_calls"):
        out[key] = total[key]
    out["ai_calls_per_message"] = round(total["ai_calls"] / total["turns"], 2)
    out["latency_p50_ms"] = out["latency_p95_ms"] = None  # not rebuildable: raw latencies are not stored in a report
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reports", nargs="+", type=Path)
    parser.add_argument("--styles", default="en,ar,mixed")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--report-out", type=Path, help="also write the combined result in the shape of a report, so `python -m agent_service eval-compare` can read it")
    parser.add_argument("--check", type=Path, help="verify one report and print its per-style counts")
    args = parser.parse_args(argv)
    if args.check:
        data = json.loads(args.check.read_text(encoding="utf-8"))
        for style, entry in data["styles"].items():
            if entry["conversations"]:
                print(style, json.dumps(counts(entry, style, args.check.name))[:300])
        return 0
    wanted = [s for s in args.styles.split(",") if s]
    loaded = [(p, json.loads(p.read_text(encoding="utf-8"))) for p in args.reports]
    for key in ("mode", "agent_mode"):
        if len({d.get(key) for _, d in loaded}) > 1:
            raise SystemExit(f"refusing to merge: the reports differ in '{key}': {[(p.name, d.get(key)) for p, d in loaded]}")
    per_style = {}
    for path, data in loaded:
        for style in wanted:
            entry = data["styles"].get(style)
            if entry and entry["conversations"]:
                if style in per_style:
                    raise SystemExit(f"style {style} appears in two reports")
                per_style[style] = counts(entry, style, path.name)
    missing = [s for s in wanted if s not in per_style]
    if missing:
        raise SystemExit(f"no report holds the style(s): {missing}")
    result = {"mode": loaded[0][1]["mode"], "agent_mode": loaded[0][1].get("agent_mode"), "sources": [p.name for p, _ in loaded],
              "per_style": per_style, "combined": combine(per_style),
              "note": "combined latency percentiles are not rebuildable from the stored reports; per-style values are kept"}
    if args.report_out:
        c = result["combined"]
        shaped = {"mode": result["mode"], "agent_mode": result["agent_mode"], "label": "merged_" + "_".join(wanted), "date": loaded[0][1].get("date"),
                  "styles": {s: d["styles"][s] for p, d in loaded for s in wanted if d["styles"].get(s, {}).get("conversations")},
                  "overall": {"style": "all", "conversations": c["conversations"], "turns": c["turns"],
                              "measures": {"solved_without_person_rate": c["solved_without_person"] / c["conversations"], "needless_handoffs": c["needless_handoffs"], "missed_escalations": c["missed_escalations"],
                                           "wrong_or_unapproved_actions": c["wrong_or_unapproved_actions"], "unbacked_facts": c["unbacked_facts"], "agent_fallbacks": c["agent_fallbacks"],
                                           "ai_calls_per_message": c["ai_calls_per_message"], "time_p50_ms": None, "time_p95_ms": None},
                              "intent_accuracy": c["intent_right"] / c["intent_total"], "decision_accuracy": c["decision_right"] / c["decision_total"],
                              "citation_accuracy": c["citation_right"] / c["citation_total"], "locale_match": c["locale_right"] / c["locale_total"],
                              "handoff_precision": c["handoff_precision_pct"] / 100 if c["handoff_precision_pct"] is not None else None,
                              "handoff_recall": c["handoff_recall_pct"] / 100 if c["handoff_recall_pct"] is not None else None},
                  "judge": None, "worst_conversations": [], "topics": {}}
        args.report_out.write_text(json.dumps(shaped, indent=2, ensure_ascii=False), encoding="utf-8")
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(json.dumps(result["combined"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
