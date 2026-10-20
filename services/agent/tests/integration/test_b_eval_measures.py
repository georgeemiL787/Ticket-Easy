"""The measures that compare two ways of running the agent, `--label`, and `eval-compare`."""

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from agent_service import __main__ as cli
from agent_service.config import Settings
from agent_service.container import build_container
from agent_service.evaluation.compare import compare_runs, judge_mean, load_run
from agent_service.evaluation.conversations import (
    EVAL_TODAY,
    TENANT,
    CountingLLM,
    EvalConversation,
    EvalTurn,
    Gold,
    evaluate,
    known_text,
    render,
    run_conversation,
    unbacked_facts,
    write_report,
)
from tests.fakes import FakeLLM

SETTINGS = Settings(llm="none", llm_rewrite=False)
RETURN = "I want to return order NS-20790, wrong size. My phone is 01123456702"


def conv(cid: str, style: str, *turns: tuple[str, Gold]) -> EvalConversation:
    return EvalConversation.model_validate(
        {
            "id": cid,
            "language_style": style,
            "turns": [EvalTurn(say=say, gold=gold).model_dump() for say, gold in turns],
        }
    )


# ---- the measures ----


async def test_solved_needless_and_missed_are_counted_from_the_run_and_the_gold() -> None:
    report = await evaluate(
        [
            conv("hello", "en", ("Hello", Gold(intents=("greeting",), decision="answer"))),  # solved
            conv("asked", "en", ("I want to talk to a human", Gold(escalation="customer_request"))),  # right handoff
            conv("missed", "en", ("Hello", Gold(decision="handoff"))),  # the gold wants a person: none came
            conv("needless", "en", ("I want to talk to a human", Gold(decision="answer"))),  # handed over for nothing
        ],
        SETTINGS,
        when=date(2026, 10, 1),
    )
    m = report.overall.measures()
    assert (m["no_person_needed"], m["solved_without_person"]) == (
        2,
        1,
    )  # hello and needless; only hello had no handoff
    assert (m["needless_handoffs"], m["missed_escalations"], m["escalations_wanted"]) == (1, 1, 2)
    assert m["solved_without_person_rate"] == 0.25 and m["unbacked_facts"] == 0
    assert m["ai_calls_per_message"] == 0 and m["wrong_or_unapproved_actions"] == 0


async def test_a_write_the_gold_expects_is_fine_and_one_it_does_not_is_wrong() -> None:
    yes = ("yes", Gold(decision="execute"))
    report = await evaluate(
        [
            conv("expected", "en", (RETURN, Gold(decision="confirm")), yes),
            conv("unexpected", "en", (RETURN, Gold(decision="confirm")), ("yes", Gold(decision="answer"))),
        ],
        SETTINGS,
        when=date(2026, 10, 1),
    )
    m = report.overall.measures()
    assert m["writes"] == 2  # both conversations wrote (each in its own chat)
    assert m["wrong_or_unapproved_actions"] == 1  # the second one was not expected by its gold
    assert m["unbacked_facts"] == 0  # the reference the shop issued ("REF-...", "RET-...") is a backed fact


async def test_the_unbacked_fact_audit_flags_an_invented_number_and_accepts_known_ones() -> None:
    container = build_container(SETTINGS.model_copy(update={"fixed_today": EVAL_TODAY, "store": "memory"}))
    assert container.orchestrator is not None
    await container.orchestrator.handle_turn(TENANT, "audit", "Hello")
    known = known_text(container)
    ok = await unbacked_facts(container, "audit", ["Hello"], "Returns are accepted within 14 days of delivery.", known)
    bad = await unbacked_facts(container, "audit", ["Hello"], "You will get EGP 9999 back by 2031-01-01.", known)
    assert ok == []
    assert any("9999" in p for p in bad) and any("2031-01-01" in p for p in bad)


async def test_today_passes_in_a_chat_with_no_facts_but_not_next_to_a_fact_or_as_another_day() -> None:
    """The live check lets small talk say "today"; the audit does the same, and nothing else loosens."""
    container = build_container(SETTINGS.model_copy(update={"fixed_today": EVAL_TODAY, "store": "memory"}))
    assert container.orchestrator is not None
    await container.orchestrator.handle_turn(TENANT, "audit2", "Hello")
    known = known_text(container)
    chat = "أنا تمام، أقدر أساعدك النهاردة؟"  # Egyptian Arabic: "I am fine, how can I help you today?"
    assert await unbacked_facts(container, "audit2", ["Hello"], chat, known) == []
    cited = await unbacked_facts(container, "audit2", ["Hello"], chat, known, ["return_policy@v2#s2"])
    assert any("النهاردة" in p for p in cited)  # next to a citation it is a claim, so it is flagged
    quoted = "Shipping costs EGP 50. [shipping_policy@v1#s0]"  # a passage the search really returned
    assert not [
        p
        for p in await unbacked_facts(container, "audit2", ["Hello"], quoted, known, ["shipping_policy@v1#s0"])
        if "citation" in p
    ]
    invented = await unbacked_facts(container, "audit2", ["Hello"], quoted, known, ["shipping_policy@v1#s3"])
    assert any("shipping_policy@v1#s0" in p for p in invented)  # cited in the text but never retrieved
    other_day = await unbacked_facts(container, "audit2", ["Hello"], "هنوصلك بكرة", known)
    assert any("بكرة" in p for p in other_day)  # another day is never excused


async def test_ai_calls_are_counted_per_message() -> None:
    llm = CountingLLM(FakeLLM({"a": 1}, {"b": 2}))
    await llm.complete_json(system="s", user="u", schema_hint={})
    await llm.complete_json(system="s", user="u", schema_hint={})
    assert llm.calls == 2
    container = build_container(SETTINGS.model_copy(update={"fixed_today": EVAL_TODAY, "store": "memory"}))
    ticks = iter(range(0, 100, 3))  # the counter goes up by 3 during every message
    results = await run_conversation(
        container, conv("c", "en", ("Hello", Gold()), ("Hello", Gold())), lambda: next(ticks)
    )
    assert [r.ai_calls for r in results] == [3, 3]


async def test_the_report_shows_the_measures_in_markdown_and_json(tmp_path: Path) -> None:
    report = await evaluate(
        [conv("hello", "en", ("Hello", Gold(decision="answer")))], SETTINGS, when=date(2026, 10, 1), label="try"
    )
    text = render(report)
    assert "Measures for comparing runs" in text and "solved without a person" in text and "unbacked facts" in text
    markdown, data = write_report(report, tmp_path)
    assert markdown.name == "eval_try_2026-10-01.md" and data.name == "eval_try_2026-10-01.json"
    saved = json.loads(data.read_text(encoding="utf-8"))
    assert saved["label"] == "try" and saved["overall"]["measures"]["solved_without_person"] == 1
    assert set(saved["styles"]["en"]["measures"]) >= {"needless_handoffs", "time_p95_ms", "ai_calls_per_message"}


# ---- the recorded baseline ----


def test_the_phase_zero_baseline_is_recorded_and_clean() -> None:
    baseline = load_run(Path(__file__).parents[2] / "eval" / "baseline_phase0.json")
    assert baseline["label"] == "phase0" and baseline["mode"] == "rules" and baseline["overall"]["conversations"] == 240
    m = baseline["overall"]["measures"]
    assert m["unbacked_facts"] == 0 and m["wrong_or_unapproved_actions"] == 0 and m["ai_calls_per_message"] == 0


# ---- eval-compare ----


def run_dict(**over: Any) -> dict[str, Any]:
    scope = {
        "conversations": 10,
        "intent_accuracy": 0.5,
        "decision_accuracy": 0.6,
        "measures": {
            "solved_without_person_rate": 0.5,
            "needless_handoffs": 4,
            "missed_escalations": 1,
            "wrong_or_unapproved_actions": 0,
            "unbacked_facts": 0,
            "ai_calls_per_message": 0.0,
            "time_p50_ms": 3.0,
            "time_p95_ms": 5.0,
        },
    }
    base = {"mode": "rules", "date": "2026-10-01", "label": "a", "overall": scope, "styles": {"en": scope}}
    return {**base, **over}


def test_compare_shows_both_runs_and_the_difference_per_measure() -> None:
    better = run_dict(label="b")
    better["overall"] = {**better["overall"], "intent_accuracy": 0.75}
    better["overall"]["measures"] = {
        **better["overall"]["measures"],
        "needless_handoffs": 1,
        "ai_calls_per_message": 2.5,
    }
    out = compare_runs(run_dict(), better, "base", "agent")
    assert "# Evaluation compared: base vs agent" in out and "### All conversations" in out
    assert "| intent accuracy | 50.0% | 75.0% | +25.0 pts | higher |" in out
    assert "| needless handoffs | 4 | 1 | -3 | lower |" in out
    assert "| AI calls per message | 0.00 | 2.50 | +2.50 | lower |" in out
    assert "### Language style: en" in out and "### Language style: ar" not in out  # only the styles both runs have


def test_compare_leaves_a_dash_for_a_measure_a_run_does_not_have() -> None:
    old = run_dict()
    del old["overall"]["measures"]  # a report from before the measures existed
    out = compare_runs(old, run_dict(), "old", "new")
    assert "| needless handoffs | — | 4 | — | lower |" in out


def test_judge_quality_is_compared_only_when_both_runs_were_judged() -> None:
    judged = run_dict(judge={"all": {f"mean_{c}": 4.0 for c in ("clarity", "politeness", "register", "helpfulness")}})
    assert judge_mean(judged, "all") == 4.0 and judge_mean(run_dict(), "all") is None
    assert "| reply quality (AI judge, 1-5) | — | 4.00 | — | higher |" in compare_runs(run_dict(), judged)


def test_the_command_compares_two_saved_runs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(run_dict()), encoding="utf-8")
    b.write_text(json.dumps(run_dict(label="b")), encoding="utf-8")
    assert cli.main(["eval-compare", str(a), str(b)]) == 0
    out = capsys.readouterr().out
    assert "Evaluation compared: a vs b" in out and "label b" in out
    (tmp_path / "bad.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="not a report"):
        cli.main(["eval-compare", str(a), str(tmp_path / "bad.json")])


def test_the_label_option_names_the_saved_report(tmp_path: Path) -> None:
    folder = tmp_path / "set"
    folder.mkdir()
    line = {"id": "en-1", "language_style": "en", "turns": [{"say": "Hello", "gold": {"decision": "answer"}}]}
    (folder / "en.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
    assert cli.main(["eval", "--set", str(folder), "--out", str(tmp_path / "out"), "--label", "phase0"]) == 0
    assert list((tmp_path / "out").glob("eval_phase0_*.json"))


# ---- a long run that stops and continues (--batch, --resume) ----


async def test_a_run_in_batches_gives_the_same_report_as_one_run(tmp_path: Path) -> None:
    from agent_service.config import Settings
    from agent_service.evaluation.conversations import evaluate, load_set, sample

    settings = Settings.from_env({"TEAM_B_LLM": "none"}).model_copy(update={"llm": "none", "roles": {}})
    conversations = sample(load_set(), 2)  # 8 conversations
    whole = await evaluate(conversations, settings)
    checkpoint = tmp_path / "partial.jsonl"
    first = await evaluate(conversations, settings, checkpoint=checkpoint, batch=3)
    assert not first.complete and first.remaining == 5 and len(checkpoint.read_text(encoding="utf-8").splitlines()) == 3
    second = await evaluate(conversations, settings, checkpoint=checkpoint, batch=3)
    assert not second.complete and second.remaining == 2
    last = await evaluate(conversations, settings, checkpoint=checkpoint, batch=3)
    assert last.complete and last.remaining == 0

    def counts(report: Any) -> dict[str, Any]:
        return {k: v for k, v in report.as_dict()["overall"]["measures"].items() if not k.startswith("time_")}

    assert counts(last) == counts(whole)  # the same numbers (how long each took differs from run to run)
    assert last.as_dict()["overall"]["intent_accuracy"] == whole.as_dict()["overall"]["intent_accuracy"]
    assert [w["id"] for w in last.worst] == [w["id"] for w in whole.worst]


async def test_a_conversation_during_which_the_budget_ran_out_is_not_recorded(tmp_path: Path) -> None:
    from agent_service.adapters.llm_limits import RateBudgetExceeded
    from agent_service.config import Settings
    from agent_service.evaluation.conversations import CountingLLM, evaluate, load_set, sample

    class Empty:
        async def complete_json(self, **kw: Any) -> dict[str, Any]:
            raise RateBudgetExceeded(30.0, "requests per minute")

    counting = CountingLLM(Empty())  # type: ignore[arg-type]
    with pytest.raises(RateBudgetExceeded):
        await counting.complete_json(system="", user="", schema_hint={})
    assert counting.budget_hit
    settings = Settings.from_env({"TEAM_B_LLM": "none"}).model_copy(update={"llm": "none", "roles": {}})
    done = await evaluate(sample(load_set(), 1), settings, checkpoint=tmp_path / "p.jsonl", batch=2)
    assert done.remaining == 2 and len((tmp_path / "p.jsonl").read_text(encoding="utf-8").splitlines()) == 2
