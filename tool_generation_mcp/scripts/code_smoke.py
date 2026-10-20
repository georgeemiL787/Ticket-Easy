"""Opt-in live code discovery/proposal check. Never executes the target project."""
import argparse
import hashlib
import json
from pathlib import Path
from team_c.config import Settings, AppError
from team_c.storage import Store
from team_c.service import Service
from team_c.providers import Providers


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--project",required=True)
    parser.add_argument("--allowed-directory",required=True)
    parser.add_argument("--description-file",required=True)
    parser.add_argument("--database",default="data/code-live.sqlite3")
    parser.add_argument("--analyze",action="store_true")
    parser.add_argument("--setting",action="append",default=[],help="Owner-confirmed non-secret route setting NAME=/prefix")
    args=parser.parse_args()
    settings=Settings(database_path=args.database,allowed_project_directory=args.allowed_directory)
    store=Store(settings.database_path);service=Service(settings,store,Providers(settings,store))
    business=service.business("Local-project live smoke",Path(args.description_file).read_text())
    try:
        confirmed=dict(item.split("=",1) for item in args.setting)
        spec=service.local_project(business["id"],args.project,confirmed)
        print(json.dumps(dict(stage="facts",spec_id=spec["id"],coverage=spec["inventory"]["coverage"],operations=len(spec["inventory"]["operations"]),supported=sum(o["supported"] for o in spec["inventory"]["operations"]),summary=spec["inventory"].get("discovery_summary"),route_settings=spec["inventory"].get("route_settings")),indent=2),flush=True)
        original=spec["inventory"]["source_hashes"]
        if args.analyze:
            spec=service.analyze_code(spec["id"])
            print(json.dumps(dict(stage="live_code_analysis",spec_id=spec["id"],run_id=spec.get("run_id"),coverage=spec["inventory"].get("analysis_coverage")),indent=2),flush=True)
        result=service.generate(spec["id"])
        print(json.dumps(dict(stage="live_proposal",provider=settings.llm_primary,model=getattr(settings,settings.llm_primary+"_model"),result=result),indent=2),flush=True)
        for pid in result["proposal_ids"]:
            p=service.view(pid)
            print(json.dumps(dict(proposal_id=pid,state=p["state"],content=p["content"],derived=p["derived"]),indent=2))
        after={rel:hashlib.sha256((Path(args.project)/rel).read_bytes()).hexdigest() for rel in original}
        if after!=original: raise RuntimeError("Source hashes changed during live check")
        print("PASS: indexed source hashes unchanged; no target imports/install/execution")
    except AppError as exc:
        print(json.dumps(dict(error=exc.code,message=exc.message,details=exc.details),indent=2))
        raise SystemExit(1)


if __name__=="__main__": main()
