"""Read-only SQL the web layer still runs directly, kept in one place so it can move to a repository."""
import json


def businesses(store):
    return store.all("SELECT * FROM businesses ORDER BY created_at DESC")


def business(store,bid):
    return store.one("SELECT * FROM businesses WHERE id=?",(bid,))


def specifications(store,bid):
    return store.all("SELECT id,filename,created_at FROM specifications WHERE business_id=? ORDER BY created_at",(bid,))


def runs(store,bid):
    return store.all("SELECT * FROM runs WHERE business_id=? ORDER BY created_at DESC",(bid,))


def proposals(store,service,bid):
    return [service.view(p["id"]) for p in store.all("SELECT id FROM proposals WHERE business_id=?",(bid,))]


def artifacts(store,pid):
    return store.all("SELECT id,version,sha256,created_at FROM artifacts WHERE proposal_id=? ORDER BY created_at",(pid,))


def run(store,rid):
    result=store.one("SELECT * FROM runs WHERE id=?",(rid,))
    for key in ("result","error"):
        result[key]=json.loads(result[key]) if result[key] else None
    result["attempts"]=store.all("SELECT * FROM attempts WHERE run_id=? ORDER BY id",(rid,))
    result["diagnostics"]=store.all("SELECT * FROM diagnostics WHERE run_id=? ORDER BY id",(rid,))
    for diagnostic in result["diagnostics"]:
        diagnostic["payload"]=json.loads(diagnostic["payload"])
    return result
