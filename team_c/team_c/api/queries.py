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


# The owner's next step for a proposal, from its current version's state and build evidence alone.
STAGES = (("review", "Needs your review"), ("build", "Approved: build and test"), ("test", "Built: test and publish"),
          ("published", "Published (test only)"), ("closed", "Closed"))


def proposal_index(store):
    """Every proposal's current version with its business and build evidence, for the cross-business overview.

    A light query: it reads states and flags only, never the full review view of each proposal.
    """
    rows = store.all("SELECT p.id AS proposal_id,p.business_id,b.name AS business,v.version,v.state,v.content,v.created_at,"
                     "EXISTS(SELECT 1 FROM artifacts a WHERE a.proposal_id=p.id AND a.version=v.version) AS built,"
                     "EXISTS(SELECT 1 FROM publications u WHERE u.proposal_id=p.id AND u.status='published') AS published "
                     "FROM proposals p JOIN versions v ON v.proposal_id=p.id AND v.version=p.current_version JOIN businesses b ON b.id=p.business_id "
                     "ORDER BY v.created_at DESC")
    result = []
    for r in rows:
        content = json.loads(r.pop("content"))
        state = r["state"]
        stage = ("review" if state in ("needs_clarification", "needs_reconciliation", "ready_for_review")
                 else "closed" if state != "approved_to_build" else "published" if r["published"] else "test" if r["built"] else "build")
        result.append(dict(r, name=content["name"], purpose=content["business_purpose"], stage=stage,
                           questions=len(content["questions"]), built=bool(r["built"]), published=bool(r["published"])))
    return result
