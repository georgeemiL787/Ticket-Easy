"""The labelled messages, the accuracy report and the guard against the word lists getting worse."""

from collections import Counter

import pytest

from team_b.__main__ import main
from team_b.brain.nlu import RuleBasedNLU
from team_b.config import Settings
from team_b.domain.session import SessionState
from team_b.domain.tenant import TenantConfig, TenantRegistry
from team_b.domain.understanding import IntentCandidate, Language, NLUResult
from team_b.nlu_eval import (
    ENTITY_TYPES,
    EvalReport,
    LabelledMessage,
    evaluate,
    load_baseline,
    load_labelled,
    render,
    save_baseline,
)

TOLERANCE = 0.02  # intent accuracy may fall at most 2 points below the recorded baseline


@pytest.fixture(scope="module")
def tenant() -> TenantConfig:
    return TenantRegistry.from_dir(Settings().config_dir).get("shop_001")


@pytest.fixture(scope="module")
def messages() -> list[LabelledMessage]:
    return load_labelled()


# ---- the data ----


def test_the_labelled_set_is_big_and_balanced(messages: list[LabelledMessage]) -> None:
    by_style = Counter(m.language for m in messages)
    assert len(messages) >= 120
    assert by_style[Language.ARABIZI] >= 40
    assert by_style[Language.AR] >= 25 and by_style[Language.MIXED] >= 25 and by_style[Language.EN] >= 25


def test_ids_are_unique_and_texts_are_not_repeated(messages: list[LabelledMessage]) -> None:
    assert len({m.id for m in messages}) == len(messages)
    assert len({m.text for m in messages}) == len(messages)


def test_every_catalog_intent_is_covered_and_every_label_is_a_real_intent(
    messages: list[LabelledMessage], tenant: TenantConfig
) -> None:
    used = Counter(i for m in messages for i in m.intents)
    assert set(used) <= set(tenant.intents), set(used) - set(tenant.intents)
    assert all(used[name] >= 4 for name in tenant.intents), {n: used[n] for n in tenant.intents}


def test_the_set_covers_the_hard_cases(messages: list[LabelledMessage]) -> None:
    assert sum(len(m.intents) >= 2 for m in messages) >= 8  # several intents in one message
    assert sum(not m.intents for m in messages) >= 8  # out of scope, negated, or just a detail
    assert sum(m.wants_human for m in messages) >= 8
    assert sum("greeting" in m.intents for m in messages) >= 6
    assert {t for m in messages for t in m.entities} == set(ENTITY_TYPES)
    for style in (Language.EN, Language.AR, Language.MIXED, Language.ARABIZI):
        assert any(not m.intents for m in messages if m.language is style), style


def test_spelling_varies_in_arabizi(messages: list[LabelledMessage]) -> None:
    texts = " ".join(m.text.lower() for m in messages if m.language is Language.ARABIZI)
    for variant in ("3ayez", "3ayza", "3awez", "3ayz", "3awz"):
        assert variant in texts, variant


def test_people_requests_are_labelled_with_the_human_intent(messages: list[LabelledMessage]) -> None:
    assert all(("human_request" in m.intents) == m.wants_human for m in messages)


# ---- the guard ----


async def test_rules_intent_accuracy_has_not_dropped(tenant: TenantConfig, messages: list[LabelledMessage]) -> None:
    baseline = load_baseline()
    assert "rules" in baseline, "record it with: python -m team_b eval-nlu --mode rules --save-baseline"
    report = await evaluate(RuleBasedNLU(), tenant, messages)
    assert report.intent.value >= baseline["rules"] - TOLERANCE, (
        f"intent accuracy {report.intent.value:.3f} is more than 2 points below the baseline {baseline['rules']:.3f}; "
        "run python -m team_b eval-nlu to see the misses"
    )


async def test_entities_are_extracted_exactly(tenant: TenantConfig, messages: list[LabelledMessage]) -> None:
    report = await evaluate(RuleBasedNLU(), tenant, messages)
    for kind in ENTITY_TYPES:
        stats = report.entities[kind]
        assert stats.precision == 1.0 and stats.recall == 1.0, (kind, stats)


async def test_language_detection_on_the_labelled_set(tenant: TenantConfig, messages: list[LabelledMessage]) -> None:
    assert (await evaluate(RuleBasedNLU(), tenant, messages)).language.value >= 0.95


# ---- the evaluator itself ----


class ScriptedNLU:
    """Answers with a fixed result per text."""

    def __init__(self, answers: dict[str, NLUResult]) -> None:
        self.answers = answers

    async def understand(self, text: str, session: SessionState | None, tenant: TenantConfig) -> NLUResult:
        return self.answers[text]


def result(intents: list[str], language: Language = Language.EN, **extra: object) -> NLUResult:
    return NLUResult.model_validate(
        {
            "language": language,
            "language_confidence": 0.9,
            "intents": tuple(IntentCandidate(name=n, confidence=0.8) for n in intents),
            **extra,
        }
    )


def message(id_: str, text: str, intents: list[str], **extra: object) -> LabelledMessage:
    return LabelledMessage.model_validate({"id": id_, "text": text, "language": "en", "intents": intents, **extra})


async def test_scoring_counts_what_it_should(tenant: TenantConfig) -> None:
    labelled = [
        message("a", "right", ["order_status"]),
        message("b", "wrong first", ["cancel_order", "refund_request"]),
        message("c", "nothing", []),
        message("d", "phone", [], entities={"phone": "01012345601", "amount": "300"}),
        message("e", "person", ["human_request"], wants_human=True),
    ]
    answers = {
        "right": result(["order_status"]),
        "wrong first": result(["refund_request", "cancel_order"]),
        "nothing": result([]),
        "phone": result([], entities={"phone": "01012345601", "order_id": "NS-20877"}),
        "person": result(["human_request"], wants_human=False),
    }
    report = await evaluate(ScriptedNLU(answers), tenant, labelled, mode="scripted")
    assert (report.intent.right, report.intent.total) == (4, 5)  # only "wrong first" has a wrong first intent
    assert (report.exact.right, report.exact.total) == (4, 5)
    assert (report.multi_exact.right, report.multi_exact.total) == (0, 1)
    assert (report.human.right, report.human.total) == (4, 5)
    phone, amount, order = (report.entities[k] for k in ("phone", "amount", "order_id"))
    assert (phone.matched, phone.predicted, phone.gold) == (1, 1, 1)
    assert (amount.matched, amount.predicted, amount.gold) == (0, 0, 1) and amount.recall == 0.0
    assert order.precision == 0.0 and order.recall is None


async def test_the_worst_misses_come_first(tenant: TenantConfig) -> None:
    labelled = [message("small", "x", ["order_status"]), message("big", "y", ["cancel_order"])]
    answers = {
        "x": result(["order_status", "greeting"]),  # only an extra intent
        "y": result(["refund_request"], language=Language.AR),  # wrong intent and language
    }
    report = await evaluate(ScriptedNLU(answers), tenant, labelled)
    assert [m.message.id for m in report.misses] == ["big", "small"]
    assert any("language" in p for p in report.misses[0].problems)


async def test_render_shows_every_number_and_the_misses(tenant: TenantConfig, messages: list[LabelledMessage]) -> None:
    text = render(await evaluate(RuleBasedNLU(), tenant, messages))
    for expected in (
        "intent accuracy",
        "multi-intent exact match",
        "language accuracy",
        "by style:",
        "arabizi",
        "precision",
        "order_id",
        "worst misses",
    ):
        assert expected in text


async def test_a_report_with_no_misses_says_so(tenant: TenantConfig) -> None:
    report = await evaluate(ScriptedNLU({"ok": result([])}), tenant, [message("a", "ok", [])])
    assert report.misses == [] and "0 worst misses" in render(report)


def test_the_baseline_round_trips_and_keeps_other_modes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "baseline.json"
    assert load_baseline(path) == {}
    rules, llm = EvalReport(mode="rules"), EvalReport(mode="llm")
    rules.intent.right, rules.intent.total = 9, 10
    llm.intent.right, llm.intent.total = 10, 10
    save_baseline(rules, path)
    save_baseline(llm, path)
    assert load_baseline(path) == {"rules": 0.9, "llm": 1.0}


# ---- the command ----


def test_the_command_runs_offline_and_prints_the_report(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["eval-nlu", "--mode", "rules"]) == 0
    out = capsys.readouterr().out
    assert "mode: rules" in out and "intent accuracy" in out and "worst misses" in out


def test_the_llm_mode_explains_what_is_missing() -> None:
    with pytest.raises(SystemExit, match="--mode llm needs an AI model"):
        main(["eval-nlu", "--mode", "llm"])


def test_the_llm_mode_builds_the_ai_assisted_understanding_when_a_model_is_configured() -> None:
    from team_b.__main__ import build_nlu
    from team_b.brain.llm_nlu import LLMNLU

    assert isinstance(build_nlu("rules", Settings(llm="ollama")), RuleBasedNLU)
    assert isinstance(build_nlu("llm", Settings(llm="ollama")), LLMNLU)
