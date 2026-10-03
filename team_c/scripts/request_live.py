"""Focused live-model check of owner tool requests and suggestions on the service-desk fixture.

Uses a fresh temporary database and the fixture's OpenAPI document (no backend is called). Runs one supported
request, one request needing an absent operation and one suggestion run (arguments select: supported absent suggestions).
Nothing is approved, built or published. The real review database is only hashed to show it is unchanged.
"""
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path
from fixtures.service_desk.app import create_app as create_fixture
from team_c.config import Settings
from team_c.models import SuggestionRunSubmission, ToolRequestSubmission
from team_c.providers import Providers
from team_c.service import Service
from team_c.storage import Store

REAL_DB = Path("data/openapi-live.sqlite3")
LABEL = "TEST-ONLY (live request check; not a business decision): "


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def main():
    before = sha(REAL_DB)
    db = Path(tempfile.mkdtemp(prefix="team-c-requests-")) / "requests.sqlite3"
    settings = Settings(database_path=str(db), connectors_file="", sandbox_hosts="")
    store = Store(str(db))
    service = Service(settings, store, Providers(settings, store))
    business = service.business("Service desk (TEST-ONLY live request check)", "Holiday lettings company. Customers report problems with their bookings (broken appliances, cleaning issues) and staff resolve them.")
    spec = service.save_openapi(business["id"], "openapi.json", "openapi.json", json.dumps(create_fixture("base", "unused").openapi()).encode(), dict(kind="upload", filename="openapi.json"))
    report = dict(database=str(db), model=f"{settings.llm_primary}/{settings.ollama_model}", results=[])
    cases = [("supported", LABEL + "Let a customer report a problem with one of their bookings, giving the booking reference, a category and a description.", "Booking ABC123, category plumbing: the shower does not drain."),
             ("absent", LABEL + "Let a customer cancel their booking and receive a refund.", "Cancel booking ABC123 and refund the deposit.")]
    selected = set(sys.argv[1:]) or {"supported", "absent", "suggestions"}
    for name, goal, example in [c for c in cases if c[0] in selected]:
        started = time.time()
        r = service.request_tool(business["id"], ToolRequestSubmission(goal=goal, examples=example, idempotency_key=f"live-{name}-{db.parent.name}"))
        report["results"].append(dict(case=name, seconds=round(time.time() - started, 1), status=r["status"], summary=(r["outcome"] or {}).get("summary"),
                                      error=r["error"], unresolved=r["unresolved"], proposals=[dict(name=p["name"], state=p["state"], operations=p["operation_ids"]) for p in r["proposals"]],
                                      runs=[dict(kind=x["kind"], status=x["status"]) for x in (store.one("SELECT kind,status FROM runs WHERE id=?", (i,)) for i in r["run_ids"])]))
    started = time.time()
    try:
        if "suggestions" not in selected:
            raise LookupError("not selected")
        batch = service.suggest(business["id"], SuggestionRunSubmission(count=3))
        report["suggestions"] = dict(seconds=round(time.time() - started, 1), coverage={k: batch["coverage"][k] for k in ("considered", "total_operations", "partial")},
                                     kept=[dict(title=s["content"]["title"], category=s["category"], operations=s["content"]["operation_ids"], missing=s["content"]["missing"],
                                                adjusted_from=s["content"].get("category_adjusted_from"), reason=s["content"]["business_reason"]) for s in batch["suggestions"]],
                                     withheld=batch["withheld"], recognized=batch["recognized"])
    except Exception as exc:
        report["suggestions"] = dict(seconds=round(time.time() - started, 1), error=f"{type(exc).__name__}: {getattr(exc, 'code', '')} {exc}")
    ops = {o["id"]: f'{o["method"]} {o["path"]}' for o in spec["inventory"]["operations"]}
    report["operations"] = ops
    report["decisions"] = store.one("SELECT COUNT(*) AS n FROM decisions")["n"]
    report["artifacts"] = store.one("SELECT COUNT(*) AS n FROM artifacts")["n"]
    report["real_db_unchanged"] = sha(REAL_DB) == before
    json.dump(report, sys.stdout, indent=2, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
