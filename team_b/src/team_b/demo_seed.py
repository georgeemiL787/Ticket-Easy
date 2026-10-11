"""Synthetic conversations for the dashboard: real turns through the real brain and the stand-ins, spread over days.

Everything here goes through the orchestrator, so every number the dashboard shows comes from genuine traces, cases and
summary rows (not hand-written rows). A clock that jumps forward between and inside conversations gives the data its
time spread, so charts, response times and case times have something to show.
"""

import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from team_b.container import Container
from team_b.domain.handoff import CaseStatus

TENANT = "shop_001"

POLICY_QUESTIONS = (
    "How many days do I have to return an item?",
    "What is your return policy?",
    "How much does shipping cost?",
    "Do you ship outside Egypt?",
    "can I pay cash on delivery?",
    "emta a2dar araga3 el montag?",
    "el shahn bekam?",
    "fi dafa3 3and el estlam?",
    "ممكن ارجع المنتج بعد كام يوم من الاستلام؟",
    "الشحن بكام؟",
    "مدة التوصيل كام؟",
    "ممكن اعمل return للمنتج بعد كام يوم من الـ delivery؟",
    "how long does delivery take",
)
UNKNOWN_QUESTIONS = (
    "Do you offer a five year warranty on electronics?",
    "Can I buy a gift card?",
    "Do you have a store in Alexandria?",
    "هل عندكم تقسيط بدون فوايد؟",
    "what is your wifi password",
    "Do you sell furniture?",
)
ORDER_QUESTIONS = ("Where is my order NS-20877?", "el order NS-20512 wasal fein?", "الاوردر NS-20877 وصل فين؟")
ACTION_REQUESTS = (
    "I want a refund for order NS-20934",
    "I want to return my order NS-20512",
    "عايز الغي الاوردر NS-20877",
    "3ayez a3mel exchange lel order NS-20512",
)
HUMAN_REQUESTS = ("I want to talk to a human", "عايز اكلم حد من خدمة العملاء", "3ayez a7ky ma3 mawzaf")
RISKY = ("someone used my card without permission, this is fraud", "I will sue you and call the consumer agency")
ANGRY = (
    "This is ridiculous!!! Worst service ever, I am so angry, stop asking me questions!!!",
    "msh ma3ool, afsha khedma!!!",
)
SMALL_TALK = ("Hello", "ahlan, ezayak", "thanks", "شكرا")
HAPPENINGS = (  # (weight, name)
    (38, "policy"), (10, "unknown"), (9, "human"), (4, "risky"), (10, "order"), (9, "action"), (6, "small_talk"),
    (4, "outage"), (4, "angry"), (6, "policy_twice"),
)  # fmt: skip


class DriftClock:
    """A clock the seeder moves. It never goes back."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def today(self) -> date:
        return self._now.date()

    def advance(self, *, seconds: float = 0, minutes: float = 0) -> None:
        self._now += timedelta(seconds=seconds, minutes=minutes)

    def jump_to(self, moment: datetime) -> None:
        if moment > self._now:
            self._now = moment


@dataclass
class SeedReport:
    conversations: int = 0
    turns: int = 0
    cases: int = 0
    resolved_cases: int = 0
    kinds: dict[str, int] = field(default_factory=dict)


async def _say(container: Container, clock: DriftClock, conversation: str, text: str, report: SeedReport) -> None:
    assert container.orchestrator is not None
    await container.orchestrator.handle_turn(TENANT, conversation, text)
    report.turns += 1
    clock.advance(seconds=5 + report.turns % 40)


async def _handle_case(container: Container, clock: DriftClock, rng: random.Random, conversation: str) -> bool:
    """A person works the case of this conversation: usually claims, replies, and often resolves it."""
    assert container.orchestrator is not None
    case = next((c for c in reversed(await container.cases.list(TENANT)) if c.conversation_id == conversation), None)
    if case is None or case.status is not CaseStatus.OPEN or rng.random() < 0.15:
        return False
    agent = rng.choice(["sara", "omar", "mona"])
    clock.advance(minutes=rng.randint(1, 40))
    await container.orchestrator.claim(case.case_id, agent)
    clock.advance(minutes=rng.randint(1, 12))
    await container.orchestrator.human_reply(case.case_id, agent, "Hello, I am looking into this for you.")
    if rng.random() < 0.8:
        clock.advance(minutes=rng.randint(2, 90))
        await container.orchestrator.resolve(case.case_id, agent, "done")
        return True
    return False


async def seed(
    container: Container, clock: DriftClock, *, conversations: int, days: int, seed: int = 1, prefix: str = "seed"
) -> SeedReport:
    """Run `conversations` synthetic conversations spread over the `days` that start at the clock's current moment."""
    rng = random.Random(seed)
    report = SeedReport()
    begin = clock.now()
    names, weights = [n for _, n in HAPPENINGS], [w for w, _ in HAPPENINGS]
    starts = sorted(begin + timedelta(seconds=rng.random() * days * 86400) for _ in range(conversations))
    for number, start in enumerate(starts):
        clock.jump_to(start)
        kind = rng.choices(names, weights)[0]
        conversation = f"{prefix}-{number:04d}"
        report.kinds[kind] = report.kinds.get(kind, 0) + 1
        if kind == "outage":
            assert container.policy_search is not None
            container.policy_search.fail_next("search_knowledge", 2)
        sentences = {
            "policy": [rng.choice(POLICY_QUESTIONS)],
            "policy_twice": [rng.choice(POLICY_QUESTIONS), rng.choice(POLICY_QUESTIONS), rng.choice(SMALL_TALK[2:])],
            "unknown": [rng.choice(UNKNOWN_QUESTIONS), rng.choice(UNKNOWN_QUESTIONS)],
            "human": [rng.choice(POLICY_QUESTIONS), rng.choice(HUMAN_REQUESTS)],
            "risky": [rng.choice(RISKY)],
            "order": [rng.choice(ORDER_QUESTIONS)],
            "action": [rng.choice(ACTION_REQUESTS)],
            "small_talk": [rng.choice(SMALL_TALK)],
            "outage": [rng.choice(POLICY_QUESTIONS)],
            "angry": [rng.choice(ANGRY)],
        }[kind]
        for text in sentences:
            await _say(container, clock, conversation, text, report)
        if container.policy_search is not None:
            container.policy_search.reset()
        case = next((c for c in await container.cases.list(TENANT) if c.conversation_id == conversation), None)
        if case is not None:
            report.cases += 1
            report.resolved_cases += int(await _handle_case(container, clock, rng, conversation))
        report.conversations += 1
    return report


def clock_for(days: int) -> DriftClock:
    """A clock `days` before the real current time, so a seed of that many days ends about now."""
    return DriftClock(datetime.now(UTC) - timedelta(days=days))
