def get(store, pub_id):
    return store.one("SELECT * FROM publications WHERE id=?", (pub_id,))


def listing(store, business_id=None, artifact_id=None):
    return store.all("SELECT * FROM publications WHERE (? IS NULL OR business_id=?) AND (? IS NULL OR artifact_id=?) ORDER BY published_at",
                     (business_id, business_id, artifact_id, artifact_id))


def published(store, business_id):
    return store.all("SELECT * FROM publications WHERE business_id=? AND status='published' ORDER BY published_at", (business_id,))


def latest_for_tool(store, name, business_id):
    rows = store.all("SELECT * FROM publications WHERE tool_name=? AND business_id=? ORDER BY published_at DESC LIMIT 1", (name, business_id))
    return rows[0] if rows else None
