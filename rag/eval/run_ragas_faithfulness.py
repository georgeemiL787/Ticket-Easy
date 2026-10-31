"""RAGAS faithfulness (statement split + NLI) on the 54-row label set.

ragas 0.4.3 does not import in this venv (it pulls langchain VertexAI). This runner
uses the same two RAGAS prompts and the same score (share of statements with verdict 1),
with local qwen3:8b and thinking off. No OpenRouter.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")

from ollama import Client

from team_a.config import settings
from team_a.eval_rag import LABELS_FILE, _mean, load_labels
from team_a.knowledge.index import TenantIndex

OUT = Path(__file__).resolve().parents[1] / "var" / "eval" / "labels_ragas.json"

STATEMENT_PROMPT = """Given a question and an answer, analyze the complexity of each sentence in the answer. Break down each sentence into one or more fully understandable statements. Ensure that no pronouns are used in any statement. Format the outputs in JSON.

Return JSON: {{"statements": ["..."]}}

Question: {question}

Answer: {answer}

JSON:
"""

NLI_PROMPT = """Your task is to judge the faithfulness of a series of statements based on a given context. For each statement you must return verdict as 1 if the statement can be directly inferred based on the context or 0 if the statement can not be directly inferred based on the context.

Return JSON: {{"statements": [{{"statement": "...", "reason": "...", "verdict": 0 or 1}}]}}

Context:
{context}

Statements:
{statements}

JSON:
"""


def _chat_json(client: Client, prompt: str) -> dict:
    res = client.chat(
        model="qwen3:8b",
        messages=[{"role": "user", "content": prompt}],
        format="json",
        options={"temperature": 0},
        think=False,
    )
    raw = res.message.content or "{}"
    return json.loads(raw)


def score_one(client: Client, question: str, answer: str, contexts: list[str]) -> tuple[float | None, str]:
    try:
        stmts = _chat_json(client, STATEMENT_PROMPT.format(question=question, answer=answer)).get("statements") or []
        if not stmts:
            return None, "no statements"
        nli = _chat_json(
            client,
            NLI_PROMPT.format(context="\n".join(contexts), statements=json.dumps(stmts, ensure_ascii=False)),
        )
        verdicts = nli.get("statements") or []
        flags = [int(v.get("verdict", 0)) for v in verdicts]
        if not flags:
            return None, "no verdicts"
        score = sum(1 for f in flags if f) / len(flags)
        bad = [v for v in verdicts if not int(v.get("verdict", 0))]
        reason = "; ".join((v.get("reason") or "")[:120] for v in bad[:3]) or "all statements supported"
        return score, reason
    except Exception as exc:
        return None, str(exc)


def main() -> None:
    samples = load_labels(LABELS_FILE, TenantIndex.load("shop_001"))
    client = Client(host=settings.ollama_url)
    scored = []
    per_style: dict[str, list[bool]] = defaultdict(list)
    for s in samples:
        score, reason = score_one(client, s.question, s.reply, s.retrieved_texts)
        row = {"id": s.id, "style": s.style, "score": score, "reason": reason, "label": s.label}
        if s.label is not None and score is not None:
            pred = 1.0 if score >= 0.5 else 0.0
            row["agree"] = pred == s.label
            per_style[s.style].append(row["agree"])
        scored.append(row)
        shown = "n/a" if score is None else f"{score:.2f}"
        mark = "" if s.label is None else f"  gold={s.label:g}"
        print(f"  ragas        {s.id:28} {shown}{mark}  {s.question[:40]}")
    labelled = [r for r in scored if "agree" in r]
    report = {
        "faithfulness": _mean([r["score"] for r in scored if r["score"] is not None]),
        "faithfulness_n": len(scored),
        "faithfulness_rows": scored,
        "agreement": _mean([float(r["agree"]) for r in labelled]),
        "agreement_n": len(labelled),
        "agreement_per_style": {k: f"{sum(v)}/{len(v)}" for k, v in sorted(per_style.items())},
        "judge": "ragas-prompts+qwen3:8b",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"faithfulness agreement vs labels     {report['agreement']:.2%} ({report['agreement_n']})")
    print(f"  per style: {report['agreement_per_style']}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
