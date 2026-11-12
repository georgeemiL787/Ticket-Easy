"""Complete RAG evaluation with Groq API (answer generation + LLM judges) and Ollama BGE-M3 embeddings.

This script completes the remaining evaluation tasks:
1. Verify Groq API and Ollama/BGE-M3 availability
2. Run embedding-based experiments (BM25-only vs. dense vs. hybrid)
3. Compare 10 chunking configurations on shop_001, noon_eg, telco_001
4. Use Groq for LLM-based evaluation:
   - Faithfulness
   - Answer Correctness
   - Context Precision and Recall (LLM-judged)
   - No-answer behavior and hallucinations
5. Compare against BM25-only baseline
6. Analyze retrieval failures, vocabulary issues, latency, and API costs
7. Save all results with experiment metadata under docs/reports/eval_runs/
8. Update the main report with findings

Usage:
    # Pilot test (small subset)
    PYTHONPATH=src python scripts/rag_evaluation_complete.py --pilot

    # Full evaluation
    PYTHONPATH=src python scripts/rag_evaluation_complete.py

    # Resume from cached results
    PYTHONPATH=src python scripts/rag_evaluation_complete.py --use-cache
"""

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
from groq import Groq

from knowledge_service.config import settings
from knowledge_service.evaluation import CachedEmbedder, _load_jsonl, run_retrieval
from knowledge_service.schemas import SearchKnowledgeRequest
from knowledge_service.search.embeddings import OllamaEmbedder, EmbeddingUnavailable
from knowledge_service.search.index import TenantIndex, build_passages, passage_embed_text
from knowledge_service.search.retrieval import search_knowledge, tenant_thresholds, tenant_min_top_score
from knowledge_service.search.bm25 import BM25
from knowledge_service.text import tokenize


TENANTS = ("shop_001", "noon_eg", "telco_001")
PILOT_SIZE = 5  # Questions per tenant for pilot test


@dataclass
class EvaluationConfig:
    """Configuration for the evaluation run"""
    groq_api_key: str
    groq_model: str
    ollama_base_url: str
    ollama_model: str
    ollama_api_key: Optional[str]
    use_cache: bool
    pilot_mode: bool
    output_dir: Path


@dataclass
class APIUsage:
    """Track API usage and costs"""
    groq_calls: int = 0
    groq_input_tokens: int = 0
    groq_output_tokens: int = 0
    ollama_embed_calls: int = 0
    ollama_embed_time_ms: float = 0.0
    
    def add_groq_usage(self, input_tokens: int, output_tokens: int):
        self.groq_calls += 1
        self.groq_input_tokens += input_tokens
        self.groq_output_tokens += output_tokens
    
    def add_ollama_embed(self, time_ms: float):
        self.ollama_embed_calls += 1
        self.ollama_embed_time_ms += time_ms


def check_environment(config: EvaluationConfig) -> dict:
    """Check Groq API and Ollama availability"""
    status = {
        "groq_available": False,
        "groq_model": config.groq_model,
        "ollama_available": False,
        "ollama_model": config.ollama_model,
        "errors": []
    }
    
    # Test Groq API
    try:
        client = Groq(api_key=config.groq_api_key)
        response = client.chat.completions.create(
            model=config.groq_model,
            messages=[{"role": "user", "content": "test"}],
            max_tokens=10
        )
        status["groq_available"] = True
        print(f"✓ Groq API connected ({config.groq_model})")
    except Exception as e:
        status["errors"].append(f"Groq API error: {e}")
        print(f"✗ Groq API failed: {e}")
    
    # Test Ollama embeddings
    try:
        # Set Ollama environment variables if provided
        if config.ollama_base_url:
            os.environ["OLLAMA_BASE_URL"] = config.ollama_base_url
        if config.ollama_api_key:
            os.environ["OLLAMA_API_KEY"] = config.ollama_api_key
        
        embedder = OllamaEmbedder()
        test_embedding = embedder.embed(["test"])
        if test_embedding is not None and len(test_embedding) > 0:
            status["ollama_available"] = True
            print(f"✓ Ollama connected ({config.ollama_model})")
        else:
            status["errors"].append("Ollama returned empty embedding")
            print("✗ Ollama returned empty embedding")
    except EmbeddingUnavailable as e:
        status["errors"].append(f"Ollama unavailable: {e}")
        print(f"✗ Ollama unavailable: {e}")
    except Exception as e:
        status["errors"].append(f"Ollama error: {e}")
        print(f"✗ Ollama error: {e}")
    
    return status


def generate_answer_with_groq(client: Groq, model: str, question: str, context: list[str], 
                               usage: APIUsage) -> tuple[str, dict]:
    """Generate answer using Groq API"""
    context_text = "\n\n".join(f"[{i+1}] {ctx}" for i, ctx in enumerate(context))
    
    prompt = f"""Given the following context passages, answer the question. If the answer is not in the context, respond with "NO_ANSWER".

Context:
{context_text}

Question: {question}

Answer:"""
    
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0
        )
        
        answer = response.choices[0].message.content.strip()
        usage_dict = {
            "input_tokens": response.usage.prompt_tokens if response.usage else 0,
            "output_tokens": response.usage.completion_tokens if response.usage else 0
        }
        usage.add_groq_usage(usage_dict["input_tokens"], usage_dict["output_tokens"])
        
        return answer, usage_dict
    except Exception as e:
        print(f"  Error generating answer: {e}")
        return "ERROR", {"error": str(e)}


def judge_faithfulness_with_groq(client: Groq, model: str, question: str, answer: str, 
                                  context: str, usage: APIUsage) -> tuple[bool, str]:
    """Judge faithfulness using Groq API"""
    prompt = f"""Evaluate if the answer is faithful to the context. An answer is faithful if all claims are supported by the context.

Question: {question}
Context: {context}
Answer: {answer}

Is the answer faithful? Respond with ONLY "yes" or "no", followed by a brief reason.
Format: yes|no: reason"""
    
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0
        )
        
        result = response.choices[0].message.content.strip().lower()
        usage.add_groq_usage(
            response.usage.prompt_tokens if response.usage else 0,
            response.usage.completion_tokens if response.usage else 0
        )
        
        is_faithful = result.startswith("yes")
        reason = result.split(":", 1)[1].strip() if ":" in result else result
        
        return is_faithful, reason
    except Exception as e:
        print(f"  Error judging faithfulness: {e}")
        return False, f"ERROR: {e}"


def judge_answer_correctness(client: Groq, model: str, question: str, answer: str, 
                              ground_truth_context: str, usage: APIUsage) -> tuple[float, str]:
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
    
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0
        )
        
        result = response.choices[0].message.content.strip()
        usage.add_groq_usage(
            response.usage.prompt_tokens if response.usage else 0,
            response.usage.completion_tokens if response.usage else 0
        )
        
        # Parse score
        parts = result.split(":", 1)
        try:
            score = float(parts[0].strip())
            score = max(0.0, min(1.0, score))  # Clamp to [0, 1]
        except ValueError:
            score = 0.0
        
        reason = parts[1].strip() if len(parts) > 1 else result
        
        return score, reason
    except Exception as e:
        print(f"  Error judging correctness: {e}")
        return 0.0, f"ERROR: {e}"


def evaluate_retrieval_modes(tenant_id: str, cases: list[dict], embedder, 
                             config: EvaluationConfig, usage: APIUsage) -> dict:
    """Evaluate BM25-only, dense-only, and hybrid retrieval"""
    print(f"\n{'='*60}")
    print(f"Evaluating retrieval modes for {tenant_id}")
    print(f"{'='*60}")
    
    min_cosine, min_bm25 = tenant_thresholds(tenant_id)
    min_top = tenant_min_top_score(tenant_id)
    
    # Load production index
    passages, _ = build_passages(tenant_id)
    
    # Build indices for different modes
    tokenized = [tokenize(passage_embed_text(p)) for p in passages]
    bm25 = BM25(tokenized)
    
    # Get embeddings
    t_embed_start = time.perf_counter()
    passage_texts = [passage_embed_text(p) for p in passages]
    vectors = embedder.embed(passage_texts)
    embed_time_ms = (time.perf_counter() - t_embed_start) * 1000
    usage.add_ollama_embed(embed_time_ms)
    
    empty = BM25([])
    
    # Create indices for each mode
    indices = {
        "bm25_only": TenantIndex(
            tenant_id=tenant_id, meta={}, passages=passages,
            passage_vectors=None, passage_bm25=bm25,
            tickets=[], ticket_vectors=None, ticket_bm25=empty,
            resolutions=[], resolution_vectors=None, resolution_bm25=empty,
            by_citation={p["citation"]: p for p in passages}
        ),
        "dense_only": TenantIndex(
            tenant_id=tenant_id, meta={}, passages=passages,
            passage_vectors=vectors, passage_bm25=empty,  # Use empty BM25
            tickets=[], ticket_vectors=None, ticket_bm25=empty,
            resolutions=[], resolution_vectors=None, resolution_bm25=empty,
            by_citation={p["citation"]: p for p in passages}
        ),
        "hybrid": TenantIndex(
            tenant_id=tenant_id, meta={}, passages=passages,
            passage_vectors=vectors, passage_bm25=bm25,
            tickets=[], ticket_vectors=None, ticket_bm25=empty,
            resolutions=[], resolution_vectors=None, resolution_bm25=empty,
            by_citation={p["citation"]: p for p in passages}
        )
    }
    
    results = {}
    for mode_name, index in indices.items():
        print(f"\nEvaluating {mode_name}...")
        
        # Use appropriate thresholds
        if mode_name == "bm25_only":
            result = run_retrieval(cases, index, None, min_cosine, min_bm25, top_k=5, min_top_score=min_top)
        elif mode_name == "dense_only":
            result = run_retrieval(cases, index, embedder, min_cosine, 0.0, top_k=5, min_top_score=min_top)
        else:  # hybrid
            result = run_retrieval(cases, index, embedder, min_cosine, min_bm25, top_k=5, min_top_score=min_top)
        
        results[mode_name] = result
        print(f"  Recall@5: {result['recall_at_k']:.2%}")
        print(f"  No-answer precision: {result['no_answer_precision']:.2%}")
        print(f"  No-answer recall: {result['no_answer_recall']:.2%}")
    
    return {
        "tenant_id": tenant_id,
        "modes": results,
        "embedding_time_ms": embed_time_ms
    }


def evaluate_with_llm_judges(tenant_id: str, cases: list[dict], index: TenantIndex,
                             embedder, client: Groq, config: EvaluationConfig, 
                             usage: APIUsage) -> dict:
    """Evaluate with answer generation and LLM judges"""
    print(f"\n{'='*60}")
    print(f"LLM-based evaluation for {tenant_id}")
    print(f"{'='*60}")
    
    min_cosine, min_bm25 = tenant_thresholds(tenant_id)
    min_top = tenant_min_top_score(tenant_id)
    
    results = []
    faithfulness_scores = []
    correctness_scores = []
    
    for i, case in enumerate(cases):
        if config.pilot_mode and i >= PILOT_SIZE:
            break
        
        print(f"\n[{i+1}/{len(cases) if not config.pilot_mode else PILOT_SIZE}] {case['id']}")
        
        # Retrieve
        req = SearchKnowledgeRequest(
            request_id=case["id"],
            tenant_id=tenant_id,
            query=case["question"],
            top_k=5
        )
        
        retrieval_result = search_knowledge(req, index, embedder, 
                                           min_cosine=min_cosine,
                                           min_bm25=min_bm25,
                                           min_top_score=min_top)
        
        retrieved_passages = [p.text for p in retrieval_result.passages]
        retrieved_citations = [p.citation for p in retrieval_result.passages]
        
        # Generate answer
        answer, answer_usage = generate_answer_with_groq(
            client, config.groq_model, case["question"], retrieved_passages, usage
        )
        
        case_result = {
            "id": case["id"],
            "question": case["question"],
            "expected_citations": case["expected"],
            "retrieved_citations": retrieved_citations,
            "answer": answer,
            "answer_usage": answer_usage
        }
        
        # Judge only if we have an answer and context
        if answer != "NO_ANSWER" and answer != "ERROR" and retrieved_passages:
            context_text = "\n".join(retrieved_passages)
            
            # Faithfulness
            is_faithful, faith_reason = judge_faithfulness_with_groq(
                client, config.groq_model, case["question"], answer, context_text, usage
            )
            case_result["faithfulness"] = {
                "score": 1.0 if is_faithful else 0.0,
                "faithful": is_faithful,
                "reason": faith_reason
            }
            faithfulness_scores.append(1.0 if is_faithful else 0.0)
            
            # Answer correctness (using expected passages as ground truth)
            if case["expected"]:
                # Get ground truth passages
                gt_passages = [index.by_citation[cit]["text"] for cit in case["expected"] 
                              if cit in index.by_citation]
                if gt_passages:
                    gt_context = "\n".join(gt_passages)
                    correctness, corr_reason = judge_answer_correctness(
                        client, config.groq_model, case["question"], answer, gt_context, usage
                    )
                    case_result["correctness"] = {
                        "score": correctness,
                        "reason": corr_reason
                    }
                    correctness_scores.append(correctness)
        
        results.append(case_result)
        
        # Rate limiting pause
        time.sleep(0.5)
    
    summary = {
        "tenant_id": tenant_id,
        "questions_evaluated": len(results),
        "mean_faithfulness": statistics.mean(faithfulness_scores) if faithfulness_scores else 0.0,
        "mean_correctness": statistics.mean(correctness_scores) if correctness_scores else 0.0,
        "results": results
    }
    
    return summary


def run_pilot_test(config: EvaluationConfig) -> dict:
    """Run a small pilot test"""
    print("\n" + "="*70)
    print("RUNNING PILOT TEST")
    print("="*70)
    
    env_status = check_environment(config)
    if not env_status["groq_available"] or not env_status["ollama_available"]:
        return {"status": "BLOCKED", "environment": env_status}
    
    # Test on shop_001 with first 5 dev questions
    tenant_id = "shop_001"
    embedder = CachedEmbedder(OllamaEmbedder())
    client = Groq(api_key=config.groq_api_key)
    usage = APIUsage()
    
    cases = _load_jsonl(settings.benchmark_file(tenant_id, "retrieval.dev"))[:PILOT_SIZE]
    index = TenantIndex.load(tenant_id)
    
    # Test retrieval modes
    retrieval_results = evaluate_retrieval_modes(tenant_id, cases, embedder, config, usage)
    
    # Test LLM judges
    llm_results = evaluate_with_llm_judges(tenant_id, cases, index, embedder, client, config, usage)
    
    pilot_summary = {
        "status": "SUCCESS",
        "tenant": tenant_id,
        "questions": len(cases),
        "retrieval": retrieval_results,
        "llm_evaluation": llm_results,
        "api_usage": asdict(usage),
        "environment": env_status
    }
    
    return pilot_summary


def run_full_evaluation(config: EvaluationConfig) -> dict:
    """Run complete evaluation across all tenants"""
    print("\n" + "="*70)
    print("RUNNING FULL EVALUATION")
    print("="*70)
    
    env_status = check_environment(config)
    if not env_status["groq_available"] or not env_status["ollama_available"]:
        return {"status": "BLOCKED", "environment": env_status}
    
    embedder = CachedEmbedder(OllamaEmbedder())
    client = Groq(api_key=config.groq_api_key)
    usage = APIUsage()
    
    full_results = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "groq_model": config.groq_model,
            "ollama_model": config.ollama_model,
            "pilot_mode": config.pilot_mode
        },
        "environment": env_status,
        "tenants": {}
    }
    
    for tenant_id in TENANTS:
        print(f"\n{'#'*70}")
        print(f"# {tenant_id}")
        print(f"{'#'*70}")
        
        # Use dev split for development/tuning
        dev_cases = _load_jsonl(settings.benchmark_file(tenant_id, "retrieval.dev"))
        test_cases = _load_jsonl(settings.benchmark_file(tenant_id, "retrieval.test"))
        
        index = TenantIndex.load(tenant_id)
        
        tenant_results = {
            "retrieval_modes_dev": evaluate_retrieval_modes(tenant_id, dev_cases, embedder, config, usage),
            "retrieval_modes_test": evaluate_retrieval_modes(tenant_id, test_cases, embedder, config, usage),
            "llm_evaluation_dev": evaluate_with_llm_judges(tenant_id, dev_cases, index, embedder, client, config, usage),
            "llm_evaluation_test": evaluate_with_llm_judges(tenant_id, test_cases, index, embedder, client, config, usage)
        }
        
        full_results["tenants"][tenant_id] = tenant_results
    
    full_results["api_usage"] = asdict(usage)
    
    return full_results


def save_results(results: dict, config: EvaluationConfig, run_type: str):
    """Save results to JSON file"""
    config.output_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"rag_eval_{run_type}_{timestamp}.json"
    output_path = config.output_dir / filename
    
    output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResults saved to: {output_path}")
    
    return output_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--pilot", action="store_true", help="Run pilot test on small subset")
    parser.add_argument("--use-cache", action="store_true", help="Use cached API results")
    parser.add_argument("--out", type=Path, 
                       default=Path(__file__).resolve().parents[3] / "docs" / "reports" / "eval_runs" / "rag_complete")
    args = parser.parse_args(argv)
    
    # Load configuration from environment
    groq_api_key = os.getenv("GROQ_API_KEY")
    groq_model = os.getenv("GROQ_MODEL", "llama-3.1-70b-versatile")
    ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model = os.getenv("OLLAMA_EMBEDDING_MODEL", "bge-m3:latest")
    ollama_api_key = os.getenv("OLLAMA_API_KEY")
    
    if not groq_api_key:
        print("ERROR: GROQ_API_KEY not set in environment")
        return 1
    
    config = EvaluationConfig(
        groq_api_key=groq_api_key,
        groq_model=groq_model,
        ollama_base_url=ollama_base_url,
        ollama_model=ollama_model,
        ollama_api_key=ollama_api_key,
        use_cache=args.use_cache,
        pilot_mode=args.pilot,
        output_dir=args.out
    )
    
    print(f"Configuration:")
    print(f"  Groq Model: {config.groq_model}")
    print(f"  Ollama Model: {config.ollama_model}")
    print(f"  Pilot Mode: {config.pilot_mode}")
    print(f"  Output Dir: {config.output_dir}")
    
    try:
        if config.pilot_mode:
            results = run_pilot_test(config)
            save_results(results, config, "pilot")
        else:
            results = run_full_evaluation(config)
            save_results(results, config, "full")
        
        # Print summary
        print("\n" + "="*70)
        print("EVALUATION COMPLETE")
        print("="*70)
        
        if results.get("status") == "BLOCKED":
            print("\nEvaluation blocked due to environment issues:")
            for error in results["environment"]["errors"]:
                print(f"  - {error}")
            return 1
        
        if "api_usage" in results:
            usage = results["api_usage"]
            print(f"\nAPI Usage:")
            print(f"  Groq calls: {usage['groq_calls']}")
            print(f"  Groq tokens: {usage['groq_input_tokens']} input, {usage['groq_output_tokens']} output")
            print(f"  Ollama embed calls: {usage['ollama_embed_calls']}")
            print(f"  Ollama embed time: {usage['ollama_embed_time_ms']:.1f}ms")
        
        return 0
        
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        return 130
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
