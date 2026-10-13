from datetime import UTC, date, datetime, timedelta

from team_b.adapters.memory_store import TickingClock


def test_the_ticking_clock_starts_at_noon_of_the_pinned_day_and_keeps_running() -> None:
    clock = TickingClock(date(2026, 9, 28))
    first = clock.now()
    assert datetime(2026, 9, 28, 12, 0, tzinfo=UTC) <= first < datetime(2026, 9, 28, 12, 0, 5, tzinfo=UTC)
    assert clock.now() >= first
    assert clock.today() == date(2026, 9, 28)
    assert clock.now() - first < timedelta(seconds=5)
