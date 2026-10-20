"""Staying inside an AI provider's free-plan limits: a call waits its turn, or is refused at once, never fails late.

A RateLimiter holds the requests-per-minute (RPM) and tokens-per-minute (TPM) budget of one model on one server. Before
a call is sent it asks for a slot with acquire(): if the budget is free the call goes ahead, if it frees up soon the
call waits (at most max_wait_s), and if it would have to wait longer acquire() raises RateBudgetExceeded at once, so the
caller can use its backup model, the rules or the template instead of making the customer wait. The tokens of a call are
estimated before it is sent and replaced by the count the server returns (settle), so the budget stays honest.

One limiter exists per (server, model): every job that uses that model shares its budget (LimiterRegistry).
"""

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from team_b.contracts.errors import UpstreamError

WINDOW_S = 60.0
SERVICE = "llm"


class RateBudgetExceeded(UpstreamError):
    """The model's per-minute budget is used up and would not free up within the longest wait we allow."""

    def __init__(self, waited_for_s: float, limit: str) -> None:
        super().__init__(
            SERVICE, "RATE_BUDGET", f"the AI model's {limit} budget is used up (a slot in {waited_for_s:.0f}s)", True
        )
        self.waited_for_s = waited_for_s


@dataclass
class Reservation:
    """A slot taken from the budget: settle() replaces its estimated tokens with the real count."""

    stamp: float
    tokens: int
    settled: bool = False


class RateLimiter:
    def __init__(
        self,
        rpm: int | None = None,
        tpm: int | None = None,
        max_wait_s: float = 4.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.rpm, self.tpm, self.max_wait_s = rpm, tpm, max_wait_s
        self._clock, self._sleep = clock, sleep
        self._used: deque[Reservation] = deque()
        self._lock = asyncio.Lock()

    @property
    def limited(self) -> bool:
        return self.rpm is not None or self.tpm is not None

    def tighten(self, rpm: int | None, tpm: int | None, max_wait_s: float) -> None:
        """Another job on the same model brings its own numbers: the smaller of each wins, so no one overspends."""
        self.rpm = min(x for x in (self.rpm, rpm) if x is not None) if self.rpm or rpm else None
        self.tpm = min(x for x in (self.tpm, tpm) if x is not None) if self.tpm or tpm else None
        self.max_wait_s = min(self.max_wait_s, max_wait_s)

    def _purge(self, now: float) -> None:
        while self._used and now - self._used[0].stamp >= WINDOW_S:
            self._used.popleft()

    def _wait_needed(self, now: float, tokens: int) -> tuple[float, str]:
        """Seconds until a call of `tokens` fits in both budgets (0: it fits now), and which budget holds it back."""
        wait, limit = 0.0, ""
        if self.rpm is not None and len(self._used) >= self.rpm:
            wait, limit = self._used[len(self._used) - self.rpm].stamp + WINDOW_S - now, "requests per minute"
        if self.tpm is not None:
            spent = sum(r.tokens for r in self._used)
            if spent + tokens > self.tpm:
                freed = 0
                for r in self._used:  # oldest first: wait for enough earlier calls to leave the window
                    freed += r.tokens
                    if spent - freed + tokens <= self.tpm:
                        need = r.stamp + WINDOW_S - now
                        if need > wait:
                            wait, limit = need, "tokens per minute"
                        break
        return max(0.0, wait), limit

    async def acquire(self, tokens: int = 0) -> Reservation:
        """Take a slot, waiting up to max_wait_s for it; RateBudgetExceeded when it would take longer."""
        if not self.limited:
            return Reservation(self._clock(), 0, settled=True)
        tokens = min(max(0, tokens), self.tpm) if self.tpm is not None else max(0, tokens)  # one call can always fit
        waited = 0.0
        while True:
            async with self._lock:
                now = self._clock()
                self._purge(now)
                wait, limit = self._wait_needed(now, tokens)
                if wait <= 0:
                    reservation = Reservation(now, tokens)
                    self._used.append(reservation)
                    return reservation
                if waited + wait > self.max_wait_s:
                    raise RateBudgetExceeded(waited + wait, limit)
            await self._sleep(wait)
            waited += wait

    def settle(self, reservation: Reservation, actual_tokens: int | None) -> None:
        """The server said how many tokens the call used: keep that, not the estimate (None: keep the estimate)."""
        if actual_tokens is not None and not reservation.settled:
            reservation.tokens = max(0, actual_tokens)
        reservation.settled = True

    def release(self, reservation: Reservation) -> None:
        """The call never reached the server (it failed to connect): give the slot back."""
        try:
            self._used.remove(reservation)
        except ValueError:
            pass


class LimiterRegistry:
    """One RateLimiter per (server, model), shared by every job that uses that model."""

    def __init__(self, max_wait_s: float = 4.0) -> None:
        self._max_wait_s = max_wait_s
        self._limiters: dict[tuple[str, str], RateLimiter] = {}

    def add(self, base_url: str, model: str, rpm: int | None, tpm: int | None) -> RateLimiter | None:
        """Register a job's numbers for its model; the limiter of the model, or None while no one limits it."""
        key = (base_url.rstrip("/"), model)
        limiter = self._limiters.get(key)
        if limiter is None:
            if rpm is None and tpm is None:
                return None
            limiter = self._limiters[key] = RateLimiter(rpm, tpm, self._max_wait_s)
        else:
            limiter.tighten(rpm, tpm, self._max_wait_s)
        return limiter

    def get(self, base_url: str, model: str) -> RateLimiter | None:
        return self._limiters.get((base_url.rstrip("/"), model))
