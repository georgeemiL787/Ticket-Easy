"""The 40-message sample for E5b: spread over intents and languages, at most 5 Arabizi, fixed seed (standard library)."""
import json
import random
from collections import defaultdict
from pathlib import Path

SEED = 20261017
TARGET = {"en": 12, "ar": 12, "mixed": 11, "arabizi": 5}
rows = [json.loads(l) for l in Path("services/agent/eval/nlu_labelled.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
rng = random.Random(SEED)
picked = []
for language, count in TARGET.items():
    by_intent = defaultdict(list)
    for r in rows:
        if r["language"] == language:
            by_intent[(r["intents"] or ["none"])[0]].append(r)
    for group in by_intent.values():
        rng.shuffle(group)
    order = sorted(by_intent)
    rng.shuffle(order)
    chosen = []
    while len(chosen) < count and any(by_intent.values()):
        for intent in order:  # one message per intent in turn, so the intents are spread
            if by_intent[intent] and len(chosen) < count:
                chosen.append(by_intent[intent].pop())
    picked += chosen
out = Path("docs/reports/eval_runs/models/sample40.jsonl")
out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in picked) + "\n", encoding="utf-8")
from collections import Counter
print(len(picked), "messages, seed", SEED, dict(Counter(r["language"] for r in picked)), "intents:", dict(Counter((r["intents"] or ["none"])[0] for r in picked)))
