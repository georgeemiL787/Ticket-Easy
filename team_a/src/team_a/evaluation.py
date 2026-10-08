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

import numpy as np

from team_a.config import settings
from team_a.knowledge.embeddings import OllamaEmbedder
from team_a.knowledge.index import TenantIndex
from team_a.knowledge.retrieval import search_knowledge, tenant_thresholds
from team_a.policy.guardrails import load_cases, run_cases
from team_a.policy.rules_store import RuleStore
from team_a.schemas import SearchKnowledgeRequest


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

    min_cosine, min_bm25 = tenant_thresholds(tenant_id)
    r = run_retrieval(cases, index, embedder, min_cosine, min_bm25)
    print(f"\nThresholds: MIN_COSINE={min_cosine} MIN_BM25={min_bm25}")
    print(f"recall@5            {r['recall_at_k']:.2%}  per style: {r['per_style']}")
    print(f"no-answer precision {r['no_answer_precision']:.2%}")
    print(f"no-answer recall    {r['no_answer_recall']:.2%}")
    for miss in r["misses"]:
        print("  MISS", *miss)
    return r


SMOKE_TYPES = ("policy_question", "refund_status")


def eval_scenarios(tenant_id: str, db_path=None) -> dict:
    """Smoke-test retrieval on a tenant's scenarios from the SQLite data layer (no threshold tuning).

    Covers every policy_question and refund_status scenario, plus any other scenario labelled
    no_evidence (e.g. a cancellation the policy does not cover). Answerable: share of expected sections
    in the top 5. no_evidence: the result must be explicitly empty.
    """
    from team_a.db import connect, repository

    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT scenario_id, type, language, customer_message, expected_decision FROM eval_scenarios "
            f"WHERE tenant_id = ? AND (type IN ({','.join('?' * len(SMOKE_TYPES))}) "
            "OR expected_decision = 'no_evidence') ORDER BY scenario_id", (tenant_id, *SMOKE_TYPES)).fetchall()
        expected = {}
        for sid, pid in conn.execute("SELECT scenario_id, passage_id FROM eval_scenario_sections "
                                     "WHERE tenant_id = ? ORDER BY scenario_id, passage_id", (tenant_id,)):
            expected.setdefault(sid, []).append(pid)
    finally:
        conn.close()

    embedder = CachedEmbedder(OllamaEmbedder())
    found = total = all_found = answerable = empty_ok = unanswerable = 0
    misses, modes = [], set()
    for r in rows:
        req = SearchKnowledgeRequest(request_id=r["scenario_id"], tenant_id=tenant_id,
                                     query=r["customer_message"], top_k=5)
        result = repository.search_knowledge(req, embedder, db_path)
        modes.add(result.retrieval_mode)
        got = [p.citation for p in result.passages]
        want = expected.get(r["scenario_id"], [])
        if r["expected_decision"] == "no_evidence":
            unanswerable += 1
            ok = not got and result.empty_reason is not None
            empty_ok += ok
            status = "ok   empty" if ok else "MISS should be empty"
        else:
            answerable += 1
            hit = [c for c in want if c in got]
            found, total = found + len(hit), total + len(want)
            all_found += len(hit) == len(want)
            status = f"{'ok  ' if len(hit) == len(want) else 'MISS'} {len(hit)}/{len(want)}"
        line = (r["scenario_id"], r["type"], r["language"], status, r["customer_message"], want, got)
        print(f"{line[0]} {line[1]:15} {line[2]:8} {status:20} expected={want} got={got}")
        if status.startswith("MISS"):
            misses.append(line)

    summary = {
        "retrieval_mode": sorted(modes),
        "section_recall_at_5": found / total if total else 0.0,
        "scenarios_all_sections_found": f"{all_found}/{answerable}",
        "no_evidence_correct": f"{empty_ok}/{unanswerable}",
        "misses": [m[0] for m in misses],
    }
    print(f"\nRetrieval mode: {', '.join(summary['retrieval_mode'])}")
    print(f"Expected-section recall@5 {summary['section_recall_at_5']:.2%} ({found}/{total} sections)")
    print(f"Scenarios with every expected section in top 5: {summary['scenarios_all_sections_found']}")
    print(f"No-evidence scenarios returned explicitly empty: {summary['no_evidence_correct']}")
    return summary


def eval_guardrails(tenant_id: str) -> bool:
    cases = load_cases(tenant_id)
    failures, unsafe = run_cases(RuleStore(tenant_id), cases)

    print(f"Guardrail cases: {len(cases) - len(failures)}/{len(cases)} passed")
    print(f"Blocked actions that executed (must be 0): {len(unsafe)} {unsafe}")
    for case_id, title, problems in failures:
        print(f"  FAIL {case_id} {title}: {'; '.join(problems)}")
    return not failures
