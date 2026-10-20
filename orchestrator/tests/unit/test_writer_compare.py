"""The writer comparison file: both writers' replies side by side, a judge's grades, and empty rating columns."""

import csv
from pathlib import Path

import pytest

from team_b import writer_compare
from team_b.config import RoleSettings, Settings
from team_b.eval_conversations import EvalConversation, EvalTurn, Gold, TurnResult
from team_b.judge import Judge
from tests.fakes import FakeLLM
from tests.support import make_settings

GRADES = {"clarity": 4, "politeness": 5, "register": 3, "helpfulness": 4}


def conversation(id_: str, style: str) -> EvalConversation:
    return EvalConversation(id=id_, language_style=style, turns=[EvalTurn(say="فين طلبي؟", gold=Gold())])  # type: ignore[arg-type]


def result(id_: str, style: str, text: str) -> TurnResult:
    return TurnResult(
        conversation=id_, style=style, index=0, said="فين طلبي؟", decision="answer", escalation=None, locale="ar",
        citations=(), intent="order_status", entities={}, text=text, latency_ms=1.0,
    )  # fmt: skip


def settings_with_egyptian(tmp_path: Path) -> Settings:
    roles = {"writer_egyptian": RoleSettings(base_url="http://127.0.0.1:11434/v1", model="nile")}
    return make_settings(tmp_path, roles=roles)


def test_the_columns_end_with_the_two_empty_rating_columns() -> None:
    cols = writer_compare.columns()
    assert cols[:6] == ["conversation", "style", "turn", "customer", "reply_default", "reply_egyptian"]
    assert cols[-2:] == ["rating_default", "rating_egyptian"] and "judge_egyptian_register" in cols


def test_the_default_run_has_no_egyptian_model(tmp_path: Path) -> None:
    settings = settings_with_egyptian(tmp_path)
    assert writer_compare.has_egyptian(settings)
    assert not writer_compare.has_egyptian(writer_compare.without_egyptian(settings))
    assert not writer_compare.has_egyptian(make_settings(tmp_path))


async def test_it_refuses_to_run_without_the_egyptian_settings(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="WRITER_EGYPTIAN"):
        await writer_compare.compare_writers([conversation("ar-01", "ar")], make_settings(tmp_path), tmp_path)


async def test_replies_of_both_writers_are_saved_side_by_side_for_ar_and_arabizi_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs: list[bool] = []

    async def fake_run(settings: Settings, conversations: list[EvalConversation]) -> dict[str, list[TurnResult]]:
        egyptian = writer_compare.has_egyptian(settings)
        runs.append(egyptian)
        return {c.id: [result(c.id, c.language_style, "مصري" if egyptian else "عادي")] for c in conversations}

    monkeypatch.setattr(writer_compare, "run_with", fake_run)
    conversations = [conversation("ar-01", "ar"), conversation("en-01", "en"), conversation("az-01", "arabizi")]
    judge = Judge(FakeLLM(GRADES, GRADES, GRADES, GRADES))
    path, rows = await writer_compare.compare_writers(
        conversations, settings_with_egyptian(tmp_path), tmp_path / "reports", judge=judge
    )
    assert runs == [False, True] and rows == 2 and path.name.startswith("writer_compare_")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        table = list(csv.DictReader(handle))
    assert [r["conversation"] for r in table] == ["ar-01", "az-01"]
    assert table[0]["reply_default"] == "عادي" and table[0]["reply_egyptian"] == "مصري"
    assert table[0]["judge_default_clarity"] == "4" and table[0]["judge_egyptian_register"] == "3"
    assert table[0]["rating_default"] == "" and table[0]["rating_egyptian"] == ""
