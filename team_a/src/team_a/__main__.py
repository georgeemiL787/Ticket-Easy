"""Team A command line.

  python -m team_a ingest --tenant shop_001 [--no-embeddings]
  python -m team_a search --tenant shop_001 "momken araga3 el 7aga?"
  python -m team_a tickets --tenant shop_001 "el order et2akhar"
  python -m team_a risk "عايز تعويض"
  python -m team_a check path/to/check_action_request.json
  python -m team_a rules list|approve|reject|edit|extract ...
  python -m team_a explain R-RETURN-14D --tenant shop_001
  python -m team_a eval-retrieval [--split dev|test] [--sweep]   (sweep: dev only)
  python -m team_a eval-guardrails
  python -m team_a eval-scenarios [--tenant noon_eg]              (needs: db build)
  python -m team_a export-schemas
  python -m team_a db build [--tenant X] [--reset] [--no-embeddings] | db stats | db query "<sql>"
"""

import argparse
import json
import sys
import uuid
from pathlib import Path

from team_a.config import ROOT


def _print(model) -> None:
    data = model.model_dump(mode="json") if hasattr(model, "model_dump") else model
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_ingest(args) -> None:
    from team_a.knowledge.embeddings import OllamaEmbedder
    from team_a.knowledge.index import build_index

    embedder = None if args.no_embeddings else OllamaEmbedder()
    _print(build_index(args.tenant, embedder))


def cmd_search(args) -> None:
    from team_a.knowledge.embeddings import OllamaEmbedder
    from team_a.knowledge.index import TenantIndex
    from team_a.knowledge.retrieval import search_knowledge
    from team_a.schemas import SearchKnowledgeRequest

    req = SearchKnowledgeRequest(request_id=f"cli-{uuid.uuid4().hex[:8]}", tenant_id=args.tenant,
                                 query=args.query, top_k=args.top_k)
    _print(search_knowledge(req, TenantIndex.load(args.tenant), OllamaEmbedder()))


def cmd_tickets(args) -> None:
    from team_a.knowledge.embeddings import OllamaEmbedder
    from team_a.knowledge.index import TenantIndex
    from team_a.knowledge.retrieval import search_past_tickets
    from team_a.schemas import SearchPastTicketsRequest

    req = SearchPastTicketsRequest(request_id=f"cli-{uuid.uuid4().hex[:8]}", tenant_id=args.tenant,
                                   query=args.query)
    _print(search_past_tickets(req, TenantIndex.load(args.tenant), OllamaEmbedder()))


def cmd_risk(args) -> None:
    from team_a.policy.risk import classify_risk
    from team_a.schemas import ClassifyRiskRequest

    req = ClassifyRiskRequest(request_id=f"cli-{uuid.uuid4().hex[:8]}", tenant_id=args.tenant,
                              message=args.message, use_llm=not args.no_llm)
    _print(classify_risk(req))


def cmd_check(args) -> None:
    from team_a.policy.check import check_action
    from team_a.policy.rules_store import RuleStore
    from team_a.schemas import CheckActionRequest

    req = CheckActionRequest.model_validate_json(Path(args.request_file).read_text(encoding="utf-8"))
    _print(check_action(req, RuleStore(req.tenant_id)))


def cmd_explain(args) -> None:
    from team_a.policy.explain import explain_rule
    from team_a.policy.rules_store import RuleStore

    _print(explain_rule(RuleStore(args.tenant), args.rule_id))


def cmd_rules(args) -> None:
    from team_a.policy.rules_store import RuleStore

    store = RuleStore(args.tenant)
    if args.rules_cmd == "list":
        for r in store.all():
            if args.status and r.approval_status != args.status:
                continue
            print(f"{r.approval_status:9} {r.rule_id:28} {r.action:24} {r.source.citation}")
    elif args.rules_cmd == "approve":
        from team_a.policy.guardrails import GuardrailRegression

        try:
            _print(store.approve(args.rule_id, args.reviewer))
        except GuardrailRegression as exc:
            sys.exit(f"Not approved. {exc}")
    elif args.rules_cmd == "reject":
        _print(store.reject(args.rule_id, args.reviewer))
    elif args.rules_cmd == "edit":
        changes = json.loads(Path(args.changes_file).read_text(encoding="utf-8"))
        _print(store.edit(args.rule_id, changes))
    elif args.rules_cmd == "extract":
        from team_a.policy.extract import extract_candidates

        added = store.add_proposed(extract_candidates(args.tenant, args.document))
        print(f"Added {len(added)} proposed rule(s). Review with: python -m team_a rules list --status proposed")
        for r in added:
            print(f"  {r.rule_id}: {r.action} <- \"{r.source.quote[:70]}\"")


def cmd_eval_retrieval(args) -> None:
    from team_a.evaluation import eval_retrieval

    eval_retrieval(args.tenant, split=args.split, sweep=args.sweep)


def cmd_eval_scenarios(args) -> None:
    from team_a.evaluation import eval_scenarios

    eval_scenarios(args.tenant)


def cmd_eval_guardrails(args) -> None:
    from team_a.evaluation import eval_guardrails

    if not eval_guardrails(args.tenant):
        sys.exit(1)


def cmd_export_schemas(_args) -> None:
    from team_a import schemas as s

    out = ROOT / "contracts" / "schemas"
    out.mkdir(parents=True, exist_ok=True)
    models = [s.SearchKnowledgeRequest, s.RetrievalResult, s.SearchPastTicketsRequest, s.PastTicketResult,
              s.Passage, s.Rule, s.CheckActionRequest, s.PolicyDecision, s.ClassifyRiskRequest,
              s.RiskAssessment, s.RuleExplanation, s.ErrorResponse,
              s.ResolvedEscalationRequest, s.ResolvedEscalation, s.ResolutionWriteResult,
              s.SearchResolutionsRequest, s.ResolutionSearchResult]
    for model in models:
        path = out / f"{model.__name__}.schema.json"
        path.write_text(json.dumps(model.model_json_schema(by_alias=True), ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")


def cmd_db(args) -> None:
    from team_a.db import loader

    if args.db_cmd == "build":
        from team_a.knowledge.embeddings import OllamaEmbedder

        embedder = None if args.no_embeddings else OllamaEmbedder()
        report = loader.build([args.tenant] if args.tenant else None, embedder, reset=args.reset)
        _print(report)
        for tenant_id, r in report["tenants"].items():
            for warning in r["warnings"]:
                print(f"WARNING {tenant_id}: {warning}", file=sys.stderr)
    elif args.db_cmd == "stats":
        counts = loader.stats()
        tenants = sorted(counts)
        tables = list(next(iter(counts.values()), {}))
        print(f"{'table':24}" + "".join(f"{t:>12}" for t in tenants))
        for table in tables:
            print(f"{table:24}" + "".join(f"{counts[t][table]:>12}" for t in tenants))
    elif args.db_cmd == "query":
        import sqlite3

        try:
            columns, rows = loader.query(args.sql, limit=args.limit)
        except sqlite3.Error as exc:
            sys.exit(f"Query failed: {exc}")
        for row in rows:
            print(json.dumps(dict(zip(columns, row)), ensure_ascii=False, default=lambda b: f"<{len(b)} bytes>"))
        print(f"({len(rows)} row(s){', limit reached' if len(rows) == args.limit else ''})", file=sys.stderr)


def main(argv: list[str] | None = None) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="team_a")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def tenant(p):
        p.add_argument("--tenant", default="shop_001")

    p = sub.add_parser("ingest"); tenant(p)
    p.add_argument("--no-embeddings", action="store_true", help="keyword-only index (no Ollama needed)")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("search"); tenant(p)
    p.add_argument("query"); p.add_argument("--top-k", type=int, default=5)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("tickets"); tenant(p)
    p.add_argument("query")
    p.set_defaults(func=cmd_tickets)

    p = sub.add_parser("risk"); tenant(p)
    p.add_argument("message"); p.add_argument("--no-llm", action="store_true")
    p.set_defaults(func=cmd_risk)

    p = sub.add_parser("check"); p.add_argument("request_file")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("explain"); tenant(p); p.add_argument("rule_id")
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser("rules"); tenant(p)
    rsub = p.add_subparsers(dest="rules_cmd", required=True)
    r = rsub.add_parser("list"); r.add_argument("--status", choices=["proposed", "approved", "rejected"])
    for name in ("approve", "reject"):
        r = rsub.add_parser(name); r.add_argument("rule_id"); r.add_argument("--reviewer", required=True)
    r = rsub.add_parser("edit"); r.add_argument("rule_id"); r.add_argument("changes_file")
    r = rsub.add_parser("extract"); r.add_argument("--document")
    p.set_defaults(func=cmd_rules)

    p = sub.add_parser("eval-retrieval"); tenant(p); p.add_argument("--sweep", action="store_true")
    p.add_argument("--split", choices=("dev", "test"), default="dev")
    p.set_defaults(func=cmd_eval_retrieval)

    p = sub.add_parser("eval-scenarios", help="retrieval smoke test on a tenant's scenarios (from the db)")
    p.add_argument("--tenant", default="noon_eg")
    p.set_defaults(func=cmd_eval_scenarios)

    p = sub.add_parser("eval-guardrails"); tenant(p)
    p.set_defaults(func=cmd_eval_guardrails)

    p = sub.add_parser("export-schemas")
    p.set_defaults(func=cmd_export_schemas)

    p = sub.add_parser("db", help="unified SQLite data layer (var/db/team_a.sqlite)")
    dsub = p.add_subparsers(dest="db_cmd", required=True)
    d = dsub.add_parser("build"); d.add_argument("--tenant", help="default: every tenant in data/corpus")
    d.add_argument("--reset", action="store_true", help="delete the database file first")
    d.add_argument("--no-embeddings", action="store_true", help="keyword-only (no Ollama needed)")
    dsub.add_parser("stats")
    d = dsub.add_parser("query"); d.add_argument("sql"); d.add_argument("--limit", type=int, default=200)
    p.set_defaults(func=cmd_db)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
