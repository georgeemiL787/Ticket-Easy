"""The reply templates: same keys everywhere, placeholders match the code, the writer fills and quotes right."""

import json
import re
from pathlib import Path

import pytest

from team_b.brain.composer import (
    LOCALES_DIR,
    PLACEHOLDERS,
    ResponseComposer,
    default_composer,
    keys_used_in,
    load_locale,
    placeholders_in,
    render,
)
from team_b.config import PROJECT_ROOT
from team_b.contracts.evidence import Passage
from team_b.contracts.policy import LocalizedText, PolicyDecision
from team_b.domain.decision import EscalationReason
from team_b.domain.understanding import Locale

COMPOSER = default_composer()
FILES = {locale: load_locale(LOCALES_DIR / locale.value) for locale in Locale}
PART_FILES = ["actions.json", "core.json", "handoff.json", "knowledge.json"]
ARABIC = re.compile(r"[؀-ۿ]")
BRAIN = PROJECT_ROOT / "src" / "team_b" / "brain"


# ---- the files ----


def test_there_is_one_folder_per_locale_with_one_file_per_owner() -> None:
    assert sorted(p.name for p in LOCALES_DIR.iterdir() if p.is_dir() and p.name != "__pycache__") == [
        "ar",
        "arabizi",
        "en",
    ]
    for locale in Locale:
        assert sorted(p.name for p in (LOCALES_DIR / locale.value).glob("*.json")) == PART_FILES


@pytest.mark.parametrize("name", PART_FILES)
def test_every_file_has_the_same_keys_in_every_locale(name: str) -> None:
    keys = {
        locale: set(json.loads((LOCALES_DIR / locale.value / name).read_text(encoding="utf-8"))) for locale in Locale
    }
    assert keys[Locale.AR] == keys[Locale.EN] == keys[Locale.ARABIZI], name


def test_the_same_key_in_two_files_of_one_locale_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "core.json").write_text('{"greeting": "hi", "thanks": "ty"}', encoding="utf-8")
    (tmp_path / "actions.json").write_text('{"thanks": "thank you", "action_done": "done"}', encoding="utf-8")
    with pytest.raises(ValueError, match="'thanks' is defined in both actions.json and core.json"):
        load_locale(tmp_path)


def test_files_of_a_locale_are_merged_and_an_empty_folder_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "core.json").write_text('{"greeting": "hi"}', encoding="utf-8")
    (tmp_path / "handoff.json").write_text('{"handoff_generic": "bye"}', encoding="utf-8")
    assert load_locale(tmp_path) == {"greeting": "hi", "handoff_generic": "bye"}
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        load_locale(empty)


def test_every_locale_has_exactly_the_same_keys() -> None:
    english = set(FILES[Locale.EN])
    for locale in (Locale.AR, Locale.ARABIZI):
        assert set(FILES[locale]) == english, (locale, english ^ set(FILES[locale]))


def test_the_key_count_is_the_one_in_the_report() -> None:
    assert {locale.value: len(texts) for locale, texts in FILES.items()} == {"en": 59, "ar": 59, "arabizi": 59}


@pytest.mark.parametrize("locale", list(Locale))
def test_every_text_is_a_clean_sentence(locale: Locale) -> None:
    for key, text in FILES[locale].items():
        assert text.strip() == text and text, key
        assert "  " not in text, key
        assert text.count("{") == text.count("}"), key


@pytest.mark.parametrize("locale", list(Locale))
def test_placeholders_match_the_contract_in_every_locale(locale: Locale) -> None:
    for key, text in FILES[locale].items():
        assert placeholders_in(text) == PLACEHOLDERS.get(key, frozenset()), (locale.value, key)


def test_the_contract_only_names_keys_that_exist() -> None:
    assert set(PLACEHOLDERS) <= set(FILES[Locale.EN])


def test_scripts_match_the_locale() -> None:
    for key in FILES[Locale.EN]:
        assert not ARABIC.search(FILES[Locale.EN][key]), key
        assert not ARABIC.search(FILES[Locale.ARABIZI][key]), key
        assert ARABIC.search(FILES[Locale.AR][key]), key


def test_the_arabizi_texts_use_the_digit_letters_people_type() -> None:
    joined = " ".join(FILES[Locale.ARABIZI].values())
    for digit in "237":
        assert digit in joined, digit


def test_the_examples_from_the_brief_are_there() -> None:
    assert "هعملك المرتجع" in FILES[Locale.AR]["confirm_action_create_return"]
    assert "ha3mellak el return" in FILES[Locale.ARABIZI]["confirm_action_create_return"]


# ---- the keys the code uses ----


def test_every_literal_key_in_the_brain_exists_in_every_locale() -> None:
    used: set[str] = set()
    for path in BRAIN.glob("*.py"):
        used |= keys_used_in(path.read_text(encoding="utf-8"))
    assert used, "the scan found no keys: the pattern is out of date"
    for locale in Locale:
        assert used <= COMPOSER.keys(locale), (locale, used - COMPOSER.keys(locale))


def test_there_is_a_handoff_text_for_every_escalation_reason() -> None:
    for reason in EscalationReason:
        for locale in Locale:
            assert COMPOSER.has(locale, f"handoff_{reason.value}"), (reason, locale)
    assert all(COMPOSER.has(locale, "handoff_generic") for locale in Locale)


def test_there_is_a_question_for_every_slot_the_shop_asks() -> None:
    config = json.loads((PROJECT_ROOT / "config" / "tenants" / "shop_001.json").read_text(encoding="utf-8-sig"))
    slots = {s for spec in config["intents"].values() for s in spec["required_slots"]} | {"phone"}
    for slot in slots:
        assert all(COMPOSER.has(locale, f"ask_{slot}") for locale in Locale), slot


def test_there_is_a_confirmation_for_every_action_tool_and_a_default() -> None:
    tools = json.loads((PROJECT_ROOT / "fixtures" / "shop_001" / "tools.json").read_text(encoding="utf-8"))["tools"]
    config = json.loads((PROJECT_ROOT / "config" / "tenants" / "shop_001.json").read_text(encoding="utf-8-sig"))
    allowed = set(config["permissions"]["allowed_tools"])
    for tool in tools:
        if tool["name"] in allowed and tool["operation_kind"] != "read":
            assert all(COMPOSER.has(locale, f"confirm_action_{tool['capability']}") for locale in Locale), tool["name"]
    assert all(COMPOSER.has(locale, "confirm_action_default") for locale in Locale)


def test_there_is_a_label_for_every_order_status() -> None:
    backend = json.loads((PROJECT_ROOT / "fixtures" / "shop_001" / "backend.json").read_text(encoding="utf-8"))
    statuses = {o["order_status"] for o in backend["orders"]}
    assert statuses
    for status in statuses:
        assert all(COMPOSER.has(locale, f"status_{status}") for locale in Locale), status


def test_the_templates_the_task_lists_all_exist() -> None:
    required = [
        "greeting", "thanks", "ask_order_id", "ask_phone", "ask_item", "ask_reason", "ask_rephrase",
        "disambiguate_intent", "disambiguate_order", "identity_failed", "order_status_in_transit",
        "late_policy_note", "confirm_action_default", "action_done", "action_cancelled", "action_failed",
        "policy_denied", "approval_pending", "human_will_reply", "lookup_failed", "unsupported", "out_of_scope",
        "dependency_down",
    ]  # fmt: skip
    for locale in Locale:
        assert set(required) <= COMPOSER.keys(locale), locale


# ---- t() ----


def test_t_fills_the_placeholders() -> None:
    assert COMPOSER.t(Locale.EN, "action_done", reference="RET-30001") == "Done! Your reference number is RET-30001."
    ar = COMPOSER.t(Locale.AR, "confirm_action_create_refund", order_id="NS-20745", amount=1250)
    assert "NS-20745" in ar and "1250" in ar
    az = COMPOSER.t(Locale.ARABIZI, "order_status_delivered", order_id="NS-20512", date="8 Sep")
    assert "NS-20512" in az and "8 Sep" in az


def test_extra_values_are_ignored_and_missing_ones_are_a_bug() -> None:
    assert COMPOSER.t(Locale.EN, "greeting", unused="x") == FILES[Locale.EN]["greeting"]
    with pytest.raises(KeyError, match="needs reference"):
        COMPOSER.t(Locale.EN, "action_done")
    with pytest.raises(KeyError, match="no template 'nope' for locale 'ar'"):
        COMPOSER.t(Locale.AR, "nope")


def test_t_does_not_trip_over_braces_in_values() -> None:
    assert COMPOSER.t(Locale.EN, "ask_generic", slot="{surprise}") == "Could you tell me your {surprise}?"


def test_t_first_uses_the_first_key_that_exists() -> None:
    first = COMPOSER.t_first(
        Locale.EN, ["confirm_action_create_refund", "confirm_action_default"], order_id="NS-1", amount=5, action="x"
    )
    assert first.startswith("I can refund EGP 5")
    fallback = COMPOSER.t_first(Locale.EN, ["confirm_action_unheard_of", "confirm_action_default"], action="do it")
    assert fallback.startswith("I can do this for you: do it")
    with pytest.raises(KeyError, match="no template among"):
        COMPOSER.t_first(Locale.EN, ["a", "b"])


def test_the_render_shortcut_takes_the_key_first() -> None:
    assert render("greeting", Locale.EN) == FILES[Locale.EN]["greeting"]


def test_a_composer_needs_all_locales(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no texts for locale"):
        ResponseComposer({Locale.EN: {}})
    with pytest.raises(FileNotFoundError):
        ResponseComposer.from_dir(tmp_path)


# ---- policy text ----


def passage(pid: str, text: str) -> Passage:
    return Passage(
        passage_id=pid, document_id=pid.split("@")[0], version="v2", section="s2", language="ar", text=text, score=1.0
    )


def test_passages_are_quoted_exactly_with_their_citation() -> None:
    quote = "يحق للعميل استرجاع المنتج خلال 14 يومًا من تاريخ الاستلام."
    block = COMPOSER.passage_block(
        [passage("return_policy@v2#s2", quote), passage("shipping_policy@v1#s6", "EGP 100 voucher.")]
    )
    assert block == f"“{quote}” [return_policy@v2#s2]\n“EGP 100 voucher.” [shipping_policy@v1#s6]"
    assert quote in block  # byte for byte, diacritics and all
    assert COMPOSER.passage_block([]) == ""


def decision(user_message: LocalizedText | None) -> PolicyDecision:
    return PolicyDecision(request_id="r", decision="deny", reason_code="R-REFUND-14D", user_message=user_message)


def test_the_policy_message_follows_the_customers_language_and_arabizi_gets_arabic() -> None:
    message = LocalizedText(en="Returns are accepted within 14 days.", ar="مدة الاسترجاع 14 يوم.")
    assert COMPOSER.policy_message(Locale.EN, decision(message)) == "Returns are accepted within 14 days."
    assert COMPOSER.policy_message(Locale.AR, decision(message)) == "مدة الاسترجاع 14 يوم."
    assert COMPOSER.policy_message(Locale.ARABIZI, decision(message)) == "مدة الاسترجاع 14 يوم."


def test_without_a_policy_message_the_generic_sentence_is_used() -> None:
    for locale in Locale:
        assert COMPOSER.policy_message(locale, decision(None)) == FILES[locale]["policy_denied"]
    blank = LocalizedText(en="  ", ar="")
    assert COMPOSER.policy_message(Locale.EN, decision(blank)) == FILES[Locale.EN]["policy_denied"]
