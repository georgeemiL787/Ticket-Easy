import json
from ..util import dump, now


def current_contents(store, business_id):
    rows = store.all("SELECT p.id AS proposal_id,v.version,v.state,v.content FROM proposals p JOIN versions v ON v.proposal_id=p.id AND v.version=p.current_version "
                     "WHERE p.business_id=? ORDER BY v.created_at", (business_id,))
    return [dict(r, content=json.loads(r["content"])) for r in rows]


def version_content(store, pid, version):
    return json.loads(store.one("SELECT content FROM versions WHERE proposal_id=? AND version=?", (pid, version))["content"])


def latest_answers(c, pid, version):
    return {a["question_id"]: dict(a) for a in c.execute("SELECT * FROM answers WHERE proposal_id=? AND version=? ORDER BY id", (pid, version))}


def replace_version(c, pid, version, spec_id, content, fields, run, state):
    """Supersede `version` by version+1 and make it current."""
    new_version = version + 1
    c.execute("UPDATE versions SET state='superseded',review_revision=review_revision+1 WHERE proposal_id=? AND version=?", (pid, version))
    c.execute("UPDATE proposals SET current_version=? WHERE id=?", (new_version, pid))
    c.execute("INSERT INTO versions(proposal_id,version,spec_id,content,derived,state,run_id,created_at) VALUES(?,?,?,?,?,?,?,?)", (pid, new_version, spec_id, dump(content), dump(fields), state, run, now()))
    return new_version


def copy_answers(c, pid, version, answers):
    for a in answers:
        c.execute("INSERT INTO answers(proposal_id,version,question_id,text,reviewer,created_at) VALUES(?,?,?,?,?,?)", (pid, version, a["question_id"], a["text"], a["reviewer"], now()))
