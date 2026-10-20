def get(store, promotion_id):
    rows = store.all("SELECT * FROM promotions WHERE id=?", (promotion_id,))
    return rows[0] if rows else None


def for_publication(store, publication_id):
    rows = store.all("SELECT * FROM promotions WHERE publication_id=? ORDER BY created_at DESC LIMIT 1", (publication_id,))
    return rows[0] if rows else None


def listing(store, business_id=None, artifact_id=None):
    return store.all("SELECT * FROM promotions WHERE (? IS NULL OR business_id=?) AND (? IS NULL OR source_artifact_id=? OR production_artifact_id=?) ORDER BY created_at DESC",
                     (business_id, business_id, artifact_id, artifact_id, artifact_id))
