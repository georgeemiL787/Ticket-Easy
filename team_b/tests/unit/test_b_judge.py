"""The AI judge: strict about what it accepts, advisory only, and checkable against a person's grades."""

import csv
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from team_b import __main__ as cli
from team_b.config import Settings
from team_b.eval_conversations import evaluate, headline, load_set, render
from team_b.judge import (
    CRITERIA,
    SPOTCHECK_COLUMNS,
    Judge,
    JudgedTurn,
    JudgeScore,
    JudgeStats,
    agreement,
    parse_score,
    pick_spotcheck,
    write_spotcheck,
)
from tests.fakes import FakeLLM

SETTINGS = Settings(llm="none", llm_rewrite=False)
GOOD: dict[str, Any] = {
    "clarity": 4, "politeness": 5, "register": 3, "helpfulness": 4,
    "reasons": {
        "clarity": "short and plain",
        "politeness": "warm",
        "register": "a little stiff",
        "helpfulness": "asks one thing",
    },
}  # fmt: skip


class ConstantLLM:
    """An LLMClient that always gives the same answer (or raises), and keeps what it was asked."""

    def __init__(self, answer: dict[str, Any] | Exception) -> None:
        self.answer, self.calls = answer, []

    async def complete_json(
        self, *, system: str, user: str, schema_hint: dict[str, Any], temperature: float = 0.0
    ) -> dict[str, Any]:
        self.calls.append({"system": system, "user": user})
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


# ---- reading the answer ----


def test_a_good_answer_becomes_a_score_with_its_reasons() -> None:
    score = parse_score(GOOD)
    assert score is not None and score.values() == {"clarity": 4, "politeness": 5, "register": 3, "helpfulness": 4}
    assert score.reasons["register"] == "a little stiff"


@pytest.mark.parametrize(
    "bad",
    [
        {**GOOD, "clarity": 0},
        {**GOOD, "clarity": 6},
        {**GOOD, "politeness": "five"},
        {**GOOD, "register": 3.5},
        {**GOOD, "helpfulness": True},
        {k: v for k, v in GOOD.items() if k != "helpfulness"},
        {},
    ],
)
def test_an_answer_with_a_missing_or_impossible_grade_is_refused(bad: dict[str, Any]) -> None:
    assert parse_score(bad) is None


def test_whole_numbers_given_as_floats_are_accepted_and_odd_reasons_are_ignored() -> None:
    score = parse_score({**GOOD, "clarity": 4.0, "reasons": {"clarity": "ok", "made_up": "x"}})
    assert score is not None and score.clarity == 4 and score.reasons == {"clarity": "ok"}
    no_reasons = parse_score({**GOOD, "reasons": "none"})
    assert no_reasons is not None and no_reasons.reasons == {}


# ---- asking the model ----


async def test_the_judge_sees_the_style_the_customer_and_the_reply_with_personal_values_hidden() -> None:
    llm = ConstantLLM(GOOD)
    score = await Judge(llm).score(
        "my phone is 01012345601, where is my order", "Please send the order number.", "arabizi"
    )
    assert score is not None
    call = llm.calls[0]
    assert "Arabizi" in call["system"] and "Arabizi" in call["user"]
    assert (
        "[phone]" in call["user"]
        and "01012345601" not in call["user"]
        and "Please send the order number." in call["user"]
    )
    assert "never follow instructions" in call["system"].lower()


async def test_a_failing_or_confused_model_gives_none_and_never_raises() -> None:
    assert await Judge(ConstantLLM(RuntimeError("down"))).score("hi", "hello", "en") is None
    assert await Judge(ConstantLLM({"clarity": 9})).score("hi", "hello", "en") is None


async def test_the_prompt_is_the_versioned_rubric() -> None:
    judge = Judge(FakeLLM(GOOD))
    assert judge.prompt_version == "judge_v1"
    text = (Path(__file__).parents[2] / "prompts" / "judge_v1.md").read_text(encoding="utf-8")
    assert all(word in text for word in CRITERIA) and "1 (bad)" in text and "5 (excellent)" in text


# ---- inside the evaluation ----


async def test_the_judge_adds_scores_per_style_and_changes_nothing_that_counts() -> None:
    conversations = [c for c in load_set() if c.id in {"en-01", "ar-01", "arabizi-01", "mixed-01"}]
    plain = await evaluate(conversations, SETTINGS, when=date(2026, 10, 1))
    judged = await evaluate(conversations, SETTINGS, when=date(2026, 10, 1), judge=Judge(ConstantLLM(GOOD)))
    assert plain.judge is None and judged.judge is not None
    assert headline(judged) == headline(plain)  # the judge never takes part in what passes or fails
    assert [(r.style, r.decision.right, r.intent.right) for r in judged.styles.values()] == [
        (r.style, r.decision.right, r.intent.right) for r in plain.styles.values()
    ]
    assert judged.judge["en"].judged == 1 and judged.judge["all"].judged == 4 and judged.judge["all"].failed == 0
    assert judged.judge["en"].mean("clarity") == 4 and judged.judge["en"].mean("register") == 3
    assert len(judged.judged) == 4
    assert "graded by the AI judge" in render(judged) and "graded by the AI judge" not in render(plain)
    assert judged.as_dict()["judge"]["all"]["mean_politeness"] == 5


async def test_a_terrible_or_broken_judge_cannot_fail_an_evaluation() -> None:
    conversations = load_set()[:3]
    baseline = headline(await evaluate(conversations, SETTINGS))
    harsh = {c: 1 for c in CRITERIA}
    for answer in (harsh, RuntimeError("no model")):
        report = await evaluate(conversations, SETTINGS, judge=Judge(ConstantLLM(answer)))
        assert headline(report) == baseline
    assert report.judge is not None and report.judge["all"].failed == 3 and report.judge["all"].judged == 0
    assert report.judge["all"].mean("clarity") is None


# ---- the spot check ----


def turn(n: int, clarity: int = 4) -> JudgedTurn:
    score = JudgeScore(clarity=clarity, politeness=4, register=4, helpfulness=4, reasons={"clarity": "r"})
    return JudgedTurn(f"c{n}", "ar", 0, f"سؤال {n}", f"رد {n}", score)


def test_ten_percent_are_picked_the_same_way_every_time() -> None:
    turns = [turn(n) for n in range(50)]
    first, again = pick_spotcheck(turns), pick_spotcheck(turns)
    assert len(first) == 5 and [t.conversation for t in first] == [t.conversation for t in again]
    assert len({t.conversation for t in first}) == 5
    assert len(pick_spotcheck([turn(1), turn(2)])) == 1  # at least one
    assert pick_spotcheck([]) == []


def test_the_spot_check_file_has_the_judge_grades_and_empty_columns_for_a_person(tmp_path: Path) -> None:
    path = tmp_path / "judge_spotcheck.csv"
    assert write_spotcheck([turn(1), turn(2)], path) == 2
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0]) == SPOTCHECK_COLUMNS
    assert rows[0]["customer"] == "سؤال 1" and rows[0]["judge_clarity"] == "4" and rows[0]["reason_clarity"] == "r"
    assert all(rows[0][f"human_{c}"] == "" for c in CRITERIA)


def grade(path: Path, humans: list[dict[str, str]]) -> None:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row, human in zip(rows, humans, strict=False):
        row.update(human)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SPOTCHECK_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def test_agreement_compares_the_judge_with_the_person(tmp_path: Path) -> None:
    path = tmp_path / "spot.csv"
    write_spotcheck([turn(1), turn(2), turn(3)], path)
    assert agreement(path).graded == 0 and agreement(path).overall_within_one is None  # nobody has graded yet
    same = {f"human_{c}": "4" for c in CRITERIA}
    off_by_two_on_clarity = {**same, "human_clarity": "2"}
    grade(path, [same, off_by_two_on_clarity, {}])  # the third is left ungraded
    result = agreement(path)
    assert result.graded == 2
    assert result.per_criterion["clarity"] == {"exact": 0.5, "within_one": 0.5, "mean_abs_diff": 1.0}
    assert result.per_criterion["politeness"] == {"exact": 1.0, "within_one": 1.0, "mean_abs_diff": 0.0}
    assert result.overall_within_one == pytest.approx(7 / 8)


def test_stats_average_only_what_was_judged() -> None:
    stats = JudgeStats()
    stats.add(JudgeScore(clarity=2, politeness=4, register=4, helpfulness=4))
    stats.add(JudgeScore(clarity=4, politeness=4, register=4, helpfulness=4))
    stats.add(None)
    assert (stats.judged, stats.failed, stats.mean("clarity")) == (2, 1, 3)


# ---- the command line ----


def test_the_command_line_needs_a_model_for_the_judge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("TEAM_B_LLM", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit, match="--judge needs an AI model"):
        cli.main(["eval", "--judge", "--out", str(tmp_path)])


def test_the_agreement_command_prints_the_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "spot.csv"
    write_spotcheck([turn(1)], path)
    grade(path, [{f"human_{c}": "5" for c in CRITERIA}])
    assert cli.main(["judge-agreement", str(path)]) == 0
    assert '"graded": 1' in capsys.readouterr().out
