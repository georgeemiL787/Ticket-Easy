"""Per-tenant retrieval settings: thresholds from the manifest, synonyms from data/synonyms/<tenant>.json."""

import dataclasses

from team_a import config
from team_a.knowledge import retrieval
from team_a.knowledge.retrieval import tenant_thresholds
from team_a.schemas import SearchKnowledgeRequest
from team_a.text import expand_query, normalize


def test_tenant_without_override_uses_the_global_thresholds(monkeypatch):
    assert tenant_thresholds("shop_001") == (config.settings.min_cosine, config.settings.min_bm25)
    monkeypatch.setattr(retrieval, "settings", dataclasses.replace(config.settings, min_cosine=0.5, min_bm25=9.0))
    assert tenant_thresholds("shop_001") == (0.5, 9.0)  # env/global still drives tenants without an override


def test_tenant_override_comes_from_its_manifest():
    assert tenant_thresholds("noon_eg") == (0.6, 4.0)
    assert tenant_thresholds("no_such_tenant") == (config.settings.min_cosine, config.settings.min_bm25)


def test_explicit_thresholds_still_win(built_db):
    from team_a.db import repository

    req = SearchKnowledgeRequest(request_id="t", tenant_id="noon_eg", query="EMI refund to the same credit card")
    strict = repository.search_knowledge(req, None, built_db, min_bm25=1000.0)
    assert strict.passages == [] and strict.empty_reason == "below_threshold"
    assert repository.search_knowledge(req, None, built_db).passages


def test_confidence_floor_is_off_unless_configured(built_db, monkeypatch):
    from team_a.db import repository
    from team_a.knowledge.retrieval import tenant_min_top_score

    assert tenant_min_top_score("shop_001") == 0.0 and tenant_min_top_score("noon_eg") == 0.0
    req = SearchKnowledgeRequest(request_id="t", tenant_id="noon_eg", query="EMI refund to the same credit card")
    normal = repository.search_knowledge(req, None, built_db)
    assert normal.passages
    floored = repository.search_knowledge(req, None, built_db, min_top_score=normal.passages[0].score + 0.01)
    assert floored.passages == [] and floored.empty_reason == "below_threshold"  # the existing empty contract
    monkeypatch.setattr(retrieval, "_manifest_thresholds", lambda _t: (None, None, 0.99))
    assert repository.search_knowledge(req, None, built_db).passages == []  # manifest value applies


def test_top_score_picker_prefers_a_plateau_over_a_spike():
    from team_a.evaluation import SWEEP_TOP, pick_top_score

    correct = dict(zip(SWEEP_TOP, [5, 5, 6, 6, 6, 5, 5, 5, 5, 5]))
    assert pick_top_score([(t, {"correct": c}) for t, c in correct.items()]) == 0.48


def test_tenant_synonyms_apply_only_to_that_tenant():
    query = "دافع بالتقسيط"
    assert "emi" in normalize(expand_query(query, "noon_eg"))
    assert "emi" not in normalize(expand_query(query, "shop_001"))
    assert expand_query(query) == expand_query(query, "shop_001")  # shop has no tenant file: unchanged


def test_shared_synonyms_still_apply_for_a_tenant_with_its_own_file():
    assert "upper egypt" in expand_query("التوصيل للصعيد", "noon_eg")


def test_arabic_question_reaches_the_english_noon_passage(built_db):
    from team_a.db import repository

    req = SearchKnowledgeRequest(request_id="t", tenant_id="noon_eg", query="الحفاضات ينفع ترجع؟")
    citations = [p.citation for p in repository.search_knowledge(req, None, built_db).passages]
    assert "noon_return_policy@v1#s10" in citations
