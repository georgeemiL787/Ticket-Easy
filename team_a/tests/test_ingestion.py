from team_a.config import settings
from team_a.knowledge.index import build_passages
from team_a.knowledge.parsers import parse

CORPUS = settings.corpus_dir("shop_001")


def test_markdown_sections_keep_their_numbers():
    keys = [s.key for s in parse(CORPUS / "return_policy_v2.md")]
    assert keys == ["s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8"]


def test_pdf_docx_xlsx_are_parsed_into_sections():
    assert [s.key for s in parse(CORPUS / "shipping_policy.pdf")][1:] == [f"s{i}" for i in range(1, 10)]
    assert [s.key for s in parse(CORPUS / "refund_policy.docx")][1:] == [f"s{i}" for i in range(1, 7)]
    faq = parse(CORPUS / "faq.xlsx")
    assert faq[0].key == "q01" and "answer_en" in faq[0].text


def test_passages_carry_tenant_version_and_citation():
    records, hashes = build_passages("shop_001")
    s2 = next(r for r in records if r["citation"] == "return_policy@v2#s2")
    assert s2["tenant_id"] == "shop_001" and s2["current"] and s2["language"] == "ar"
    assert "14" in s2["text"]
    old = next(r for r in records if r["citation"] == "return_policy@v1#s2")
    assert not old["current"]
    assert set(hashes) >= {"faq.xlsx", "shipping_policy.pdf"}


def test_ingestion_is_reproducible():
    first, _ = build_passages("shop_001")
    second, _ = build_passages("shop_001")
    assert first == second
