"""BM25-only RAG evaluation with Groq LLM judges (no embeddings required).

This script runs when embeddings are unavailable (Ollama blocked, no local instance).
It evaluates:
1. BM25-only retrieval on both dev and test splits
2. Answer generation using Groq API (test split only)
3. LLM-based judges (faithfulness, answer correctness) using Groq
4. Failure analysis (missed retrievals, hallucinations, by language style)
5. API usage tracking and cost estimation

Usage:
    PYTHONPATH=src python scripts/rag_eval_bm25_groq.py
"""

import hashlib
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from groq import Groq

from knowledge_service.config import settings
from knowledge_service.evaluation import _load_jsonl
from knowledge_service.search.bm25 import BM25
from knowledge_service.search.index import TenantIndex, build_passages, passage_embed_text
from knowledge_service.text import tokenize


TENANTS = ("shop_001", "noon_eg", "telco_001")
GROQ_FREE_TIER_COST = {
    "input_per_1m": 0.59,  # USD per 1M input tokens
    "output_per_1m": 0.79  # USD per 1M output tokens
}


def load_cache(cache_path: Path) -> dict:
    """Load Groq API response cache"""
    if not cache_path.exists():
        return {}
    
    cache = {}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    entry = json.loads(line)
                    key = (entry["question_hash"], entry["prompt_type"])
                    cache[key] = entry
    except Exception as e:
        print(f"Warning: Failed to load cache: {e}")
    
    return cache


def save_cache_entry(cache_path: Path, entry: dict):
    """Append entry to cache file"""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def question_hash(question: str) -> str:
    """Generate stable hash for question"""
    return hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]


def call_groq_with_cache(client: Groq, model: str, prompt: str, question: str, 
                         prompt_type: str, cache: dict, cache_path: Path,
                         max_tokens: int = 500) -> tuple[str, dict]:
    """Call Groq API with caching and rate limiting"""
    q_hash = question_hash(question)
    cache_key = (q_hash, prompt_type)
    
    # Check cache
    if cache_key in cache:
        cached = cache[cache_key]
        return cached["response"], cached["usage"]
    
    # Rate limiting (reduced for faster execution)
    time.sleep(0.2)
    
    # Call API with retry on 429
    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0
            )
            
            result = response.choices[0].message.content.strip()
            usage_dict = {
                "input_tokens": response.usage.prompt_tokens if response.usage else 0,
                "output_tokens": response.usage.completion_tokens if response.usage else 0
            }
            
            # Cache the result
            cache_entry = {
                "question_hash": q_hash,
                "prompt_type": prompt_type,
                "response": result,
                "usage": usage_dict,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            save_cache_entry(cache_path, cache_entry)
            cache[cache_key] = cache_entry
            
            return result, usage_dict
            
        except Exception as e:
            if "429" in str(e) and attempt == 0:
                print(f"  Rate limited, retrying in 5s...")
                time.sleep(5.0)
                continue
            print(f"  Error calling Groq: {e}")
            return "ERROR", {"error": str(e), "input_tokens": 0, "output_tokens": 0}
    
    return "ERROR", {"error": "Failed after retry", "input_tokens": 0, "output_tokens": 0}


def generate_answer(client: Groq, model: str, question: str, context: list[str],
                    cache: dict, cache_path: Path) -> tuple[str, dict]:
    """Generate answer using Groq API"""
    context_text = "\n\n".join(f"[{i+1}] {ctx}" for i, ctx in enumerate(context))
    
    prompt = f"""Given the following context passages, answer the question. If the answer is not in the context, respond with "NO_ANSWER".

Context:
{context_text}

Question: {question}

Answer:"""
    
    return call_groq_with_cache(client, model, prompt, question, "answer_generation", 
                                cache, cache_path, max_tokens=500)


def judge_faithfulness(client: Groq, model: str, question: str, answer: str,
                       context: str, cache: dict, cache_path: Path) -> tuple[bool, str]:
    """Judge faithfulness using Groq API"""
    prompt = f"""Evaluate if the answer is faithful to the context. An answer is faithful if all claims are supported by the context.

Question: {question}
Context: {context}
Answer: {answer}

Is the answer faithful? Respond with ONLY "yes" or "no", followed by a brief reason.
Format: yes|no: reason"""
    
    result, _ = call_groq_with_cache(client, model, prompt, question + "|" + answer, 
                                     "faithfulness_judge", cache, cache_path, max_tokens=200)
    
    result_lower = result.lower()
    is_faithful = result_lower.startswith("yes")
    reason = result.split(":", 1)[1].strip() if ":" in result else result
    
    return is_faithful, reason


def judge_correctness(client: Groq, model: str, question: str, answer: str,
                     ground_truth_context: str, cache: dict, cache_path: Path) -> tuple[float, str]:
    """Judge answer correctness (0-1 scale) using Groq API"""
    prompt = f"""Evaluate the correctness of the answer given the question and ground truth context.

Question: {question}
Ground Truth Context: {ground_truth_context}
Answer: {answer}

Rate the answer correctness on a scale of 0.0 to 1.0 where:
- 1.0 = Completely correct and addresses the question fully
- 0.5 = Partially correct but missing key information
- 0.0 = Incorrect or completely off-topic

Respond with ONLY a number between 0.0 and 1.0, followed by a brief reason.
Format: 0.X: reason"""
    
    result, _ = call_groq_with_cache(client, model, prompt, question + "|" + answer,
                                     "correctness_judge", cache, cache_path, max_tokens=200)
    
    # Parse score
    parts = result.split(":", 1)
    try:
        score = float(parts[0].strip())
        score = max(0.0, min(1.0, score))  # Clamp to [0, 1]
    except ValueError:
        score = 0.0
    
    reason = parts[1].strip() if len(parts) > 1 else result
    
    return score, reason


def detect_language_style(text: str) -> str:
    """Detect language style: ar (Arabic), arabizi (romanized), or en (English)"""
    # Simple heuristic based on character ranges
    arabic_chars = sum(1 for c in text if '\u0600' <= c <= '\u06FF')
    latin_chars = sum(1 for c in text if c.isalpha() and ord(c) < 256)
    
    if arabic_chars > len(text) * 0.3:
        return "ar"
    elif latin_chars > 0:
        return "arabizi" if any(c.isdigit() for c in text) else "en"
    else:
        return "en"


def run_bm25_retrieval(cases: list[dict], index: TenantIndex, top_k: int = 5) -> dict:
    """Run BM25-only retrieval evaluation"""
    results = []
    hits = 0
    total_expected = 0
    no_answer_correct = 0
    no_answer_predicted = 0
    no_answer_expected = 0
    
    for case in cases:
        query_tokens = tokenize(case["question"])
        scores = index.passage_bm25.scores(query_tokens)
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        
        retrieved_citations = [index.passages[i]["citation"] for i in top_indices if scores[i] > 0]
        expected_citations = case["expected"]
        
        # Compute recall
        if expected_citations:
            matched = sum(1 for cit in expected_citations if cit in retrieved_citations)
            hits += matched
            total_expected += len(expected_citations)
        
        # No-answer handling
        is_no_answer_expected = not expected_citations
        is_no_answer_predicted = not retrieved_citations
        
        if is_no_answer_expected:
            no_answer_expected += 1
            if is_no_answer_predicted:
                no_answer_correct += 1
        
        if is_no_answer_predicted:
            no_answer_predicted += 1
        
        results.append({
            "id": case["id"],
            "question": case["question"],
            "expected": expected_citations,
            "retrieved": retrieved_citations,
            "hit": matched if expected_citations else None
        })
    
    recall_at_k = hits / total_expected if total_expected > 0 else 0.0
    no_answer_precision = no_answer_correct / no_answer_predicted if no_answer_predicted > 0 else 0.0
    no_answer_recall = no_answer_correct / no_answer_expected if no_answer_expected > 0 else 0.0
    
    return {
        "recall_at_k": recall_at_k,
        "no_answer_precision": no_answer_precision,
        "no_answer_recall": no_answer_recall,
        "n_questions": len(cases),
        "results": results
    }


def evaluate_tenant(tenant_id: str, client: Groq, model: str, cache: dict, 
                    cache_path: Path, api_usage: dict) -> dict:
    """Evaluate one tenant on both dev and test splits"""
    print(f"\n{'='*70}")
    print(f"Evaluating {tenant_id}")
    print(f"{'='*70}")
    
    # Load benchmark splits
    dev_path = settings.benchmark_file(tenant_id, "retrieval.dev")
    test_path = settings.benchmark_file(tenant_id, "retrieval.test")
    
    if not dev_path.exists() or not test_path.exists():
        print(f"  Warning: Benchmark files missing for {tenant_id}")
        return {"error": "Benchmark files not found"}
    
    dev_cases = _load_jsonl(dev_path)
    test_cases = _load_jsonl(test_path)
    
    print(f"  Dev: {len(dev_cases)} questions")
    print(f"  Test: {len(test_cases)} questions")
    
    # Build BM25-only index
    passages, _ = build_passages(tenant_id)
    tokenized = [tokenize(passage_embed_text(p)) for p in passages]
    bm25 = BM25(tokenized)
    
    empty = BM25([])
    index = TenantIndex(
        tenant_id=tenant_id,
        meta={},
        passages=passages,
        passage_vectors=None,
        passage_bm25=bm25,
        tickets=[],
        ticket_vectors=None,
        ticket_bm25=empty,
        resolutions=[],
        resolution_vectors=None,
        resolution_bm25=empty,
        by_citation={p["citation"]: p for p in passages}
    )
    
    # Evaluate retrieval on both splits
    print("\n  Running retrieval evaluation (dev)...")
    dev_retrieval = run_bm25_retrieval(dev_cases, index)
    print(f"    Recall@5: {dev_retrieval['recall_at_k']:.2%}")
    
    print("\n  Running retrieval evaluation (test)...")
    test_retrieval = run_bm25_retrieval(test_cases, index)
    print(f"    Recall@5: {test_retrieval['recall_at_k']:.2%}")
    
    # Generate answers and judge on test split only
    print("\n  Generating answers and judging (test split)...")
    faithfulness_scores = []
    correctness_scores = []
    failure_cases = []
    
    for i, case in enumerate(test_cases):
        if (i + 1) % 10 == 0:
            print(f"    [{i+1}/{len(test_cases)}] Progress checkpoint...", flush=True)
        elif i < 5 or i >= len(test_cases) - 2:
            print(f"    [{i+1}/{len(test_cases)}] {case['id']}", flush=True)
        
        # Get retrieval result
        retrieval_result = next(r for r in test_retrieval["results"] if r["id"] == case["id"])
        retrieved_citations = retrieval_result["retrieved"]
        
        # Analyze retrieval failures
        if case["expected"] and retrieval_result["hit"] < len(case["expected"]):
            failure_cases.append({
                "id": case["id"],
                "question": case["question"],
                "style": detect_language_style(case["question"]),
                "expected": case["expected"],
                "got": retrieved_citations,
                "failure_type": "missed_retrieval"
            })
        
        # Skip answer generation if no context
        if not retrieved_citations:
            if case["expected"]:
                failure_cases.append({
                    "id": case["id"],
                    "question": case["question"],
                    "style": detect_language_style(case["question"]),
                    "expected": case["expected"],
                    "got": [],
                    "failure_type": "hallucination_gap"
                })
            continue
        
        # Get context
        context_passages = [index.by_citation[cit]["text"] for cit in retrieved_citations]
        
        # Generate answer
        answer, answer_usage = generate_answer(client, model, case["question"], 
                                               context_passages, cache, cache_path)
        
        api_usage["groq_calls"] += 1
        api_usage["input_tokens"] += answer_usage.get("input_tokens", 0)
        api_usage["output_tokens"] += answer_usage.get("output_tokens", 0)
        
        if answer in ("NO_ANSWER", "ERROR"):
            if case["expected"]:
                failure_cases.append({
                    "id": case["id"],
                    "question": case["question"],
                    "style": detect_language_style(case["question"]),
                    "expected": case["expected"],
                    "got": retrieved_citations,
                    "failure_type": "no_answer_returned"
                })
            continue
        
        # Judge faithfulness
        context_text = "\n".join(context_passages)
        is_faithful, faith_reason = judge_faithfulness(client, model, case["question"],
                                                       answer, context_text, cache, cache_path)
        faithfulness_scores.append(1.0 if is_faithful else 0.0)
        
        api_usage["groq_calls"] += 1
        api_usage["input_tokens"] += 0  # Already counted in cache
        api_usage["output_tokens"] += 0
        
        # Judge correctness
        if case["expected"]:
            gt_passages = [index.by_citation[cit]["text"] for cit in case["expected"] 
                          if cit in index.by_citation]
            if gt_passages:
                gt_context = "\n".join(gt_passages)
                correctness, corr_reason = judge_correctness(client, model, case["question"],
                                                            answer, gt_context, cache, cache_path)
                correctness_scores.append(correctness)
                
                api_usage["groq_calls"] += 1
                api_usage["input_tokens"] += 0  # Already counted in cache
                api_usage["output_tokens"] += 0
    
    mean_faithfulness = statistics.mean(faithfulness_scores) if faithfulness_scores else 0.0
    mean_correctness = statistics.mean(correctness_scores) if correctness_scores else 0.0
    
    print(f"\n    Faithfulness: {mean_faithfulness:.2%} (n={len(faithfulness_scores)})")
    print(f"    Correctness: {mean_correctness:.2%} (n={len(correctness_scores)})")
    
    return {
        "dev": {
            "recall_at_5": dev_retrieval["recall_at_k"],
            "no_answer_precision": dev_retrieval["no_answer_precision"],
            "no_answer_recall": dev_retrieval["no_answer_recall"],
            "n_questions": dev_retrieval["n_questions"]
        },
        "test": {
            "recall_at_5": test_retrieval["recall_at_k"],
            "no_answer_precision": test_retrieval["no_answer_precision"],
            "no_answer_recall": test_retrieval["no_answer_recall"],
            "n_questions": test_retrieval["n_questions"],
            "mean_faithfulness": mean_faithfulness,
            "mean_correctness": mean_correctness,
            "n_judged": len(correctness_scores)
        },
        "failure_cases": failure_cases,
        "dev_results": dev_retrieval["results"],
        "test_results": test_retrieval["results"]
    }


def main() -> int:
    # Load environment
    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    
    groq_api_key = os.getenv("GROQ_API_KEY")
    groq_model = os.getenv("GROQ_MODEL", "llama-3.1-70b-versatile")
    
    if not groq_api_key:
        print("ERROR: GROQ_API_KEY not set in environment")
        return 1
    
    print(f"Configuration:")
    print(f"  Groq Model: {groq_model}")
    print(f"  Retrieval Mode: BM25-only (embeddings unavailable)")
    
    # Setup paths
    output_dir = Path(__file__).resolve().parents[3] / "docs" / "reports" / "eval_runs" / "rag_complete"
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_path = output_dir / "groq_cache.jsonl"
    
    # Load cache
    cache = load_cache(cache_path)
    print(f"  Cache: {len(cache)} entries loaded")
    
    # Initialize Groq client
    client = Groq(api_key=groq_api_key)
    
    # Track API usage
    api_usage = {
        "groq_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0
    }
    
    # Evaluate all tenants
    all_results = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "groq_model": groq_model,
        "embeddings": "unavailable",
        "retrieval_mode": "bm25_only",
        "tenants": {},
        "failure_analysis": {}
    }
    
    for tenant_id in TENANTS:
        try:
            tenant_result = evaluate_tenant(tenant_id, client, groq_model, cache, 
                                           cache_path, api_usage)
            all_results["tenants"][tenant_id] = {
                "dev": tenant_result["dev"],
                "test": tenant_result["test"]
            }
            all_results["failure_analysis"][tenant_id] = tenant_result["failure_cases"]
            
            # Save detailed results
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            detail_path = output_dir / f"bm25_groq_eval_{timestamp}_{tenant_id}.json"
            with open(detail_path, "w", encoding="utf-8") as f:
                json.dump(tenant_result, f, ensure_ascii=False, indent=2)
            
        except Exception as e:
            print(f"\nERROR evaluating {tenant_id}: {e}")
            import traceback
            traceback.print_exc()
            all_results["tenants"][tenant_id] = {"error": str(e)}
    
    # Calculate costs
    estimated_cost = (
        api_usage["input_tokens"] / 1_000_000 * GROQ_FREE_TIER_COST["input_per_1m"] +
        api_usage["output_tokens"] / 1_000_000 * GROQ_FREE_TIER_COST["output_per_1m"]
    )
    
    all_results["api_usage"] = {
        "groq_calls": api_usage["groq_calls"],
        "input_tokens": api_usage["input_tokens"],
        "output_tokens": api_usage["output_tokens"],
        "estimated_cost_usd": round(estimated_cost, 4)
    }
    
    all_results["limitations"] = [
        "Embeddings unavailable - hybrid retrieval comparison blocked",
        "BM25-only retrieval may miss semantic matches",
        "Cannot compare dense vs. sparse retrieval strategies"
    ]
    
    # Save summary
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_path = output_dir / "bm25_groq_summary.json"
    detail_path = output_dir / f"bm25_groq_eval_{timestamp}.json"
    
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    
    with open(detail_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    
    print(f"\n{'='*70}")
    print("EVALUATION COMPLETE")
    print(f"{'='*70}")
    print(f"\nSummary saved to: {summary_path}")
    print(f"Detailed results saved to: {detail_path}")
    print(f"\nAPI Usage:")
    print(f"  Groq calls: {api_usage['groq_calls']}")
    print(f"  Input tokens: {api_usage['input_tokens']:,}")
    print(f"  Output tokens: {api_usage['output_tokens']:,}")
    print(f"  Estimated cost: ${estimated_cost:.4f}")
    
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
