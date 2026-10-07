"""Benchmarks for both Team A workstreams.

Retrieval: recall@5 on answerable questions, per writing style, plus no-answer metrics.
  The question set is split (stratified by style and answerability) into dev, used to sweep
  thresholds, and test, held out and scored once with the chosen thresholds.
  no_answer_precision = of the queries we returned empty for, the share that truly had no answer
  no_answer_recall    = of the truly unanswerable queries, the share we returned empty for
Guardrails: every case must produce the expected decision; blocked actions must never be allowed.
"""

import json
from collections import defaultdict
from datetime import date

import numpy as np

from team_a.config import settings
from team_a.knowledge.embeddings import OllamaEmbedder
from team_a.knowledge.index import TenantIndex
from team_a.knowledge.retrieval import search_knowledge
from team_a.policy.check import check_action
from team_a.policy.risk import keyword_scan
from team_a.policy.rules_store import RuleStore
from team_a.schemas import CheckActionRequest, SearchKnowledgeRequest


class CachedEmbedder:
    def __init__(self, inner):
        self.inner, self.model, self.cache = inner, inner.model, {}

    def embed(self, texts: list[str]) -> np.ndarray:
        missing = [t for t in texts if t not in self.cache]
        if missing:
            for t, v in zip(missing, self.inner.embed(missing)):
                self.cache[t] = v
        return np.stack([self.cache[t] for t in texts])


def _load_jsonl(path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def run_retrieval(cases, index, embedder, min_cosine, min_bm25, top_k=5) -> dict:
    per_style = defaultdict(lambda: [0, 0])
    empty_returned = empty_correct = unanswerable = 0
    misses = []
    for case in cases:
        req = SearchKnowledgeRequest(request_id=case["id"], tenant_id=index.tenant_id,
                                     query=case["question"], top_k=top_k)
        result = search_knowledge(req, index, embedder, min_cosine=min_cosine, min_bm25=min_bm25)
        got = [p.citation for p in result.passages]
        expected = case["expected"]
        if not got:
            empty_returned += 1
            empty_correct += not expected
        if not expected:
            unanswerable += 1
            if got:
                misses.append((case["id"], "should be empty", got[:2]))
            continue
        hit = any(c in expected for c in got)
        per_style[case["style"]][0] += hit
        per_style[case["style"]][1] += 1
        if not hit:
            misses.append((case["id"], case["question"], got[:2]))
    hits = sum(h for h, _ in per_style.values())
    total = sum(n for _, n in per_style.values())
    return {
        "correct": hits + empty_correct,  # answered with a gold passage in top-k, or correctly empty
        "recall_at_k": hits / total if total else 0.0,
        "per_style": {s: f"{h}/{n}" for s, (h, n) in sorted(per_style.items())},
        "no_answer_precision": empty_correct / empty_returned if empty_returned else 1.0,
        "no_answer_recall": empty_correct / unanswerable if unanswerable else 1.0,
        "misses": misses,
    }


SPLITS = ("dev", "test")
SWEEP_COSINE = (0.44, 0.46, 0.48, 0.50, 0.52, 0.54, 0.56, 0.58, 0.60)
SWEEP_BM25 = (1.5, 2.0, 2.5, 3.0, 4.0, 5.0)


def pick_thresholds(rows) -> tuple[float, float]:
    """Most dev questions handled correctly; among ties, the point with the most tied grid
    neighbours (centre of the plateau, not its edge), then the stricter one.

    Counting questions rather than adding recall and no-answer recall keeps a few unanswerable
    questions from outweighing many answerable ones.
    """
    scores = {(c, b): r["correct"] for c, b, r in rows}
    best = max(scores.values())
    tied = {k for k, v in scores.items() if v == best}

    def neighbours(key):
        ci, bi = SWEEP_COSINE.index(key[0]), SWEEP_BM25.index(key[1])
        steps = ((ci - 1, bi), (ci + 1, bi), (ci, bi - 1), (ci, bi + 1))
        return sum(0 <= c < len(SWEEP_COSINE) and 0 <= b < len(SWEEP_BM25)
                   and (SWEEP_COSINE[c], SWEEP_BM25[b]) in tied for c, b in steps)

    return max(tied, key=lambda k: (neighbours(k), k))


def eval_retrieval(tenant_id: str, split: str = "dev", sweep: bool = False) -> dict:
    """Thresholds are swept on the dev split only; the test split is held out for the reported number."""
    if split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}")
    if sweep and split != "dev":
        raise ValueError("--sweep runs on the dev split only; the test split must stay unseen by tuning")
    cases = _load_jsonl(settings.data_dir / "benchmark" / f"retrieval_{tenant_id}.{split}.jsonl")
    index = TenantIndex.load(tenant_id)
    embedder = CachedEmbedder(OllamaEmbedder())
    print(f"Split: {split} ({len(cases)} questions, {sum(not c['expected'] for c in cases)} unanswerable)")

    if sweep:
        print(f"{'min_cos':>8} {'min_bm25':>8} {'correct':>8} {'recall@5':>9} {'na_prec':>8} {'na_rec':>7}")
        rows = []
        for min_cos in SWEEP_COSINE:
            for min_bm25 in SWEEP_BM25:
                r = run_retrieval(cases, index, embedder, min_cos, min_bm25)
                rows.append((min_cos, min_bm25, r))
                print(f"{min_cos:>8} {min_bm25:>8} {r['correct']:>5}/{len(cases):<2} {r['recall_at_k']:>9.2f} "
                      f"{r['no_answer_precision']:>8.2f} {r['no_answer_recall']:>7.2f}")
        best_cos, best_bm25 = pick_thresholds(rows)
        print(f"\nBest on dev (most questions correct, plateau centre, then stricter): "
              f"MIN_COSINE={best_cos} MIN_BM25={best_bm25}")

    r = run_retrieval(cases, index, embedder, settings.min_cosine, settings.min_bm25)
    print(f"\nThresholds: MIN_COSINE={settings.min_cosine} MIN_BM25={settings.min_bm25}")
    print(f"recall@5            {r['recall_at_k']:.2%}  per style: {r['per_style']}")
    print(f"no-answer precision {r['no_answer_precision']:.2%}")
    print(f"no-answer recall    {r['no_answer_recall']:.2%}")
    for miss in r["misses"]:
        print("  MISS", *miss)
    return r


def eval_guardrails(tenant_id: str) -> bool:
    cases = _load_jsonl(settings.data_dir / "benchmark" / f"guardrails_{tenant_id}.jsonl")
    store = RuleStore(tenant_id)
    failures, unsafe = [], []
    for case in cases:
        risk_categories = sorted(keyword_scan(case.get("message", ""))[0])
        req = CheckActionRequest.model_validate({
            "request_id": case["id"],
            "tenant_id": tenant_id,
            "as_of": case.get("as_of", date.today().isoformat()),
            "risk_categories": risk_categories,
            **case["request"],
        })
        decision = check_action(req, store)
        expect = case["expect"]
        problems = []
        if decision.decision != expect["decision"]:
            problems.append(f"decision {decision.decision} != {expect['decision']}")
            if decision.decision == "allow":
                unsafe.append(case["id"])
        if "reason_code" in expect and decision.reason_code != expect["reason_code"]:
            problems.append(f"reason {decision.reason_code} != {expect['reason_code']}")
        evaluated = [o.rule_id for o in decision.rule_outcomes]
        if "rule_id" in expect and expect["rule_id"] not in evaluated:
            problems.append(f"rule {expect['rule_id']} not evaluated")
        if "absent_rule_id" in expect and expect["absent_rule_id"] in evaluated:
            problems.append(f"inactive rule {expect['absent_rule_id']} was evaluated")
        if decision.decision == "deny" and decision.reason_code.startswith("RULE") and not decision.citations:
            problems.append("deny without citation")
        if "mandatory_escalation" in expect and bool(risk_categories) != expect["mandatory_escalation"]:
            problems.append(f"risk {risk_categories} escalation != {expect['mandatory_escalation']}")
        if problems:
            failures.append((case["id"], case["title"], problems))

    print(f"Guardrail cases: {len(cases) - len(failures)}/{len(cases)} passed")
    print(f"Blocked actions that executed (must be 0): {len(unsafe)} {unsafe}")
    for case_id, title, problems in failures:
        print(f"  FAIL {case_id} {title}: {'; '.join(problems)}")
    return not failures
