"""E5b helpers for the knowledge service (run from team_a with PYTHONPATH=src). Nothing is stored or approved.

    rules : runs the rule extraction (the same function `python -m team_a rules extract` uses) for each shop_001
            document WITHOUT storing the candidates, and prints how many candidate rules came back.
    risk  : classifies the 40 sample messages with the AI second opinion on (the risk check), one call each.
"""
import json
import sys
import time
from pathlib import Path

from team_a.knowledge.index import load_manifest
from team_a.policy.extract import extract_candidates
from team_a.policy.risk import classify_risk
from team_a.schemas import ClassifyRiskRequest

TENANT = "shop_001"
mode = sys.argv[1]
if mode == "rules":
    # the same loop as team_a.policy.extract.extract_candidates, one passage at a time, with retries, nothing stored
    from team_a import llm
    from team_a.knowledge.index import build_passages
    from team_a.policy.extract import _system_prompt, _validate_candidate
    from team_a.usage_log import usage_job

    manifest = load_manifest(TENANT)
    effective = {(d["document_id"], d["version"]): d.get("effective_date") for d in manifest["documents"]}
    passages, _ = build_passages(TENANT)
    per_document, failed = {}, []
    for passage in (p for p in passages if p["current"]):
        prompt = f"Passage {passage['citation']} ({passage['section']}):\n{passage['text']}"
        out = None
        for attempt in range(4):
            try:
                with usage_job("rule_extraction"):
                    out = llm.complete_json(_system_prompt(), prompt)
                break
            except llm.LLMUnavailable as exc:
                print("retry", passage["citation"], attempt + 1, str(exc)[:80], flush=True)
                time.sleep(15)
        counts = per_document.setdefault(passage["document_id"], {"passages": 0, "candidate_rules": 0})
        counts["passages"] += 1
        if out is None:
            failed.append(passage["citation"])
            continue
        for raw in out.get("rules", []):
            if _validate_candidate(raw, passage, effective[(passage["document_id"], passage["version"])]) is not None:
                counts["candidate_rules"] += 1
    result = {"tenant": TENANT, "per_document": per_document, "passages_failed_after_retries": failed,
              "total_passages": sum(c["passages"] for c in per_document.values()),
              "total_candidate_rules": sum(c["candidate_rules"] for c in per_document.values())}
    import os
    Path(os.environ.get("RULES_RESULT", "../docs/reports/eval_runs/knowledge/onboarding_rules_result.json")).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
elif mode == "risk":
    rows = [json.loads(l) for l in Path("../docs/reports/eval_runs/models/sample40.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    methods = {}
    for i, row in enumerate(rows, 1):
        result = classify_risk(ClassifyRiskRequest(request_id=f"r{i}", tenant_id=TENANT, message=row["text"], use_llm=True))
        methods[result.method] = methods.get(result.method, 0) + 1
        time.sleep(3)  # stay under the free plan's requests per minute
    print("risk checks:", len(rows), "methods:", methods)
