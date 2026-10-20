"""Grouping the questions the agent could not answer."""

from datetime import UTC, datetime, timedelta

from team_b.brain.gaps import group_questions, similar, tokens

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def at(minutes: int) -> datetime:
    return NOW + timedelta(minutes=minutes)


def test_tokens_ignore_case_filler_and_arabic_letter_variants() -> None:
    assert tokens("Do you sell FURNITURE?") == frozenset({"sell", "furniture"})
    assert tokens("هل عندكم تقسيط؟") == tokens("هل عندكم تقسيط")
    assert tokens("إلغاء") == tokens("الغاء")  # alef variants


def test_similar_questions_are_one_group_with_their_count_examples_and_last_seen() -> None:
    groups = group_questions(
        [
            ("Do you sell furniture?", at(0)),
            ("do you sell furniture", at(5)),
            ("Is there furniture for sale? sell", at(9)),
            ("Can I buy a gift card?", at(3)),
        ]
    )
    assert [(g.count, g.question) for g in groups] == [(3, "Do you sell furniture?"), (1, "Can I buy a gift card?")]
    assert groups[0].last_seen == at(9) and groups[0].examples == (
        "Do you sell furniture?", "do you sell furniture", "Is there furniture for sale? sell",
    )  # fmt: skip


def test_different_questions_stay_apart_and_the_biggest_group_comes_first() -> None:
    groups = group_questions([("wifi password", at(0)), ("sell furniture", at(1)), ("sell furniture please", at(2))])
    assert [g.count for g in groups] == [2, 1]


def test_words_that_do_not_carry_meaning_never_group_questions_together() -> None:
    assert not similar(tokens("hello"), tokens("please"))  # both are filler: empty sets are never similar
    assert len(group_questions([("hello", at(0)), ("pls", at(1))])) == 2


def test_no_questions_no_groups() -> None:
    assert group_questions([]) == []
