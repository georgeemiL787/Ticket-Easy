"""The per-minute budget of an AI model: a call waits its turn, or is refused at once when the wait is too long."""

import pytest

from team_b.adapters.llm_limits import LimiterRegistry, RateBudgetExceeded, RateLimiter
from team_b.contracts.errors import UpstreamError


class Time:
    """A clock the test moves by hand; sleep() moves it."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def limiter(t: Time, rpm: int | None = None, tpm: int | None = None, max_wait: float = 4.0) -> RateLimiter:
    return RateLimiter(rpm, tpm, max_wait, clock=t.clock, sleep=t.sleep)


async def test_without_limits_nothing_waits() -> None:
    t = Time()
    lim = limiter(t)
    for _ in range(100):
        await lim.acquire(10_000)
    assert t.slept == [] and not lim.limited


async def test_calls_inside_the_budget_go_ahead_at_once() -> None:
    t = Time()
    lim = limiter(t, rpm=3)
    for _ in range(3):
        await lim.acquire(0)
    assert t.slept == []


async def test_a_call_waits_for_its_slot_when_it_frees_up_soon() -> None:
    t = Time()
    lim = limiter(t, rpm=2, max_wait=10)
    await lim.acquire(0)
    t.now += 55
    await lim.acquire(0)
    await lim.acquire(0)  # the first call leaves the window 5 seconds from now
    assert t.slept == [pytest.approx(5.0)]


async def test_a_call_that_would_wait_too_long_is_refused_at_once() -> None:
    t = Time()
    lim = limiter(t, rpm=1, max_wait=4)
    await lim.acquire(0)
    with pytest.raises(RateBudgetExceeded) as raised:
        await lim.acquire(0)  # 60 seconds to wait, 4 allowed
    assert t.slept == [] and raised.value.code == "RATE_BUDGET" and raised.value.retryable
    assert isinstance(raised.value, UpstreamError)  # every caller already treats this as "use the rules / template"
    assert "requests per minute" in raised.value.message


async def test_the_token_budget_is_kept_with_the_estimate_then_the_real_count() -> None:
    t = Time()
    lim = limiter(t, tpm=1000, max_wait=1)
    first = await lim.acquire(900)
    with pytest.raises(RateBudgetExceeded) as raised:
        await lim.acquire(200)  # 900 + 200 > 1000
    assert "tokens per minute" in raised.value.message
    lim.settle(first, 300)  # the server says the call only used 300 tokens
    await lim.acquire(200)  # now it fits: the budget is honest
    assert t.slept == []


async def test_a_call_that_never_reached_the_server_gives_its_slot_back() -> None:
    t = Time()
    lim = limiter(t, rpm=1, max_wait=0)
    slot = await lim.acquire(0)
    lim.release(slot)
    await lim.acquire(0)


async def test_one_call_bigger_than_the_whole_budget_is_still_allowed_alone() -> None:
    t = Time()
    lim = limiter(t, tpm=500)
    await lim.acquire(10_000)  # clamped to the budget: it would never fit otherwise
    assert t.slept == []


async def test_the_window_slides_so_old_calls_stop_counting() -> None:
    t = Time()
    lim = limiter(t, rpm=2, max_wait=0)
    await lim.acquire(0)
    await lim.acquire(0)
    t.now += 61
    await lim.acquire(0)
    await lim.acquire(0)


def test_roles_on_one_model_share_one_limiter_and_the_tightest_numbers_win() -> None:
    registry = LimiterRegistry(max_wait_s=4)
    first = registry.add("https://api.groq.com/openai/v1", "qwen", 25, 6000)
    again = registry.add("https://api.groq.com/openai/v1/", "qwen", 10, None)  # the same server, written differently
    assert first is again and first is not None
    assert (first.rpm, first.tpm) == (10, 6000)
    assert registry.add("https://api.groq.com/openai/v1", "gpt-oss-120b", None, None) is None  # nobody limits this one
    other = registry.add("https://api.groq.com/openai/v1", "gpt-oss-120b", 25, None)
    assert other is not first and registry.get("https://api.groq.com/openai/v1", "gpt-oss-120b") is other
    # a role with no numbers on a model that someone limits still goes through that limiter
    assert registry.add("https://api.groq.com/openai/v1", "qwen", None, None) is first
