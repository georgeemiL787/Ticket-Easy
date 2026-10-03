"""Opt-in REAL model calls; no deterministic substitutes. Saves auditable run IDs."""
import argparse
import json
import time
from pathlib import Path
from team_c.config import Settings,AppError
from team_c.storage import Store
from team_c.providers import Providers
from team_c.service import Service


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--provider",choices=["ollama","openrouter"],default="ollama")
    parser.add_argument("--example",choices=["ecommerce","room-booking","nilestay"],default="ecommerce")
    parser.add_argument("--url",help="Fetch this OpenAPI URL (host must be in OPENAPI_FETCH_HOSTS) instead of an example file")
    parser.add_argument("--business-file",help="Business description text file (required with --url)")
    parser.add_argument("--path-prefix",action="append",default=[],help="Limit the generation scope to eligible operations under this path (repeatable)")
    parser.add_argument("--database",default="data/live-smoke.sqlite3")
    args=parser.parse_args()
    settings=Settings(database_path=args.database,llm_primary=args.provider,llm_fallback="none")
    store=Store(settings.database_path)
    service=Service(settings,store,Providers(settings,store))
    root=Path(__file__).resolve().parents[1]
    if args.url:
        b=service.business("Live smoke: "+args.url,Path(args.business_file).read_text())
        spec=service.fetch(b["id"],args.url)
    else:
        name=args.example
        b=service.business("Live smoke: "+name,(root/f"examples/{name}-business.txt").read_text())
        file=name+(".yaml" if name=="room-booking" else ".json")
        spec=service.upload(b["id"],file,(root/"examples"/file).read_bytes())
    scope=[o["id"] for o in spec["inventory"]["operations"] if o.get("proposal_eligible") and any(o["path"].startswith(p) for p in args.path_prefix)] or None
    print(json.dumps(dict(spec_id=spec["id"],summary=spec["inventory"].get("summary"),scope=scope and [f'{o["method"]} {o["path"]}' for o in spec["inventory"]["operations"] if o["id"] in scope]),indent=2))
    start=time.monotonic()
    try:
        result=service.generate(spec["id"],scope)
        report=dict(live=True,provider=args.provider,model=getattr(settings,args.provider+"_model"),seconds=round(time.monotonic()-start,2),business_id=b["id"],spec_id=spec["id"],result=result)
        print(json.dumps(report,indent=2))
        for pid in result["proposal_ids"]:
            p=service.view(pid)
            print(json.dumps(dict(proposal_id=pid,content=p["content"],derived=p["derived"]),indent=2))
    except AppError as exc:
        print(json.dumps(dict(live=True,provider=args.provider,seconds=round(time.monotonic()-start,2),error=exc.code,message=exc.message,details=exc.details),indent=2))
        raise SystemExit(1)


if __name__=="__main__":main()
