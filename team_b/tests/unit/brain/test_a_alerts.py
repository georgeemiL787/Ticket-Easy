"""The alert engine: each rule opens one alert when its condition starts and resolves it when it clears."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from team_b.adapters.alert_repository import InMemoryAlertStore, SqliteAlertStore
from team_b.adapters.memory_store import InMemoryCaseStore, InMemoryTraceStore
from team_b.adapters.sqlite_store import SqliteDatabase
from team_b.brain.alerts import RULES, AlertEngine, WebhookNotifier, alert_loop
from team_b.domain.alerts import Alert
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.handoff import HandoffCase, HandoffPackage
from team_b.domain.tenant import AlertThresholds, TenantRegistry
from team_b.domain.trace import DecisionTrace, PolicyRecord, ToolCallRecord
from team_b.ports import AlertStore, AlreadyExistsError, NotFoundError

T = "shop_001"
START = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


class StepClock:
    def __init__(self) -> None:
        self.at = START

    def now(self) -> datetime:
        return self.at

    def today(self) -> date:
        return self.at.date()

    def tick(self, minutes: float) -> None:
        self.at += timedelta(minutes=minutes)


class World:
    """An engine on fresh stores, a clock the test moves, and helpers that record turns at the current time."""

    def __init__(self, thresholds: AlertThresholds | None = None, store: AlertStore | None = None) -> None:
        from tests.unit.a_helpers import tenant_registry

        self.clock = StepClock()
        self.traces = InMemoryTraceStore(self.clock)
        self.cases = InMemoryCaseStore()
        self.alerts: AlertStore = store or InMemoryAlertStore()
        self.tenants: TenantRegistry = tenant_registry(thresholds)
        self.engine = AlertEngine(
            traces=self.traces, cases=self.cases, alerts=self.alerts, clock=self.clock, tenants=self.tenants
        )
        self.n = 0

    async def turn(self, **kw: Any) -> None:
        self.n += 1
        base: dict[str, Any] = {
            "trace_id": f"t{self.n}", "request_id": f"r{self.n}", "tenant_id": T, "conversation_id": f"c{self.n}",
            "turn_index": 0, "decision": Decision.ANSWER,
        }  # fmt: skip
        kw = {**base, **kw}
        if kw.get("escalation_reason") is not None:
            kw["decision"] = Decision.HANDOFF
        await self.traces.add(DecisionTrace(**kw))

    async def open_rules(self) -> dict[str, str]:
        """rule -> key of every alert open right now."""
        return {
            (a.rule + ":" + str(a.details.get("key", ""))).rstrip(":"): a.rule
            for a in await self.alerts.list(T, open_only=True)
        }

    async def evaluate(self) -> tuple[list[str], list[str]]:
        report = await self.engine.evaluate(T)
        return [a.rule for a in report.opened], [a.rule for a in report.resolved]


def failing_write(i: int, status: str = "error") -> dict[str, Any]:
    return {
        "tool_calls": (
            ToolCallRecord(request_id=f"w{i}", tool="create_refund", operation_kind="create", status=status,
                           policy_request_id="p1", error_code=None if status == "success" else "REJECTED"),
        ),
        "policy": (PolicyRecord(request_id="p1", action="create_refund", decision="allow", reason_code="X"),),
    }  # fmt: skip


# ---- every rule opens once and resolves when it clears ----


async def test_service_down_opens_after_three_failures_in_five_minutes_then_resolves() -> None:
    w = World()
    for _ in range(2):
        await w.turn(errors=("policy search unavailable: UpstreamError",))
    assert await w.evaluate() == ([], [])  # two are not enough
    await w.turn(errors=("policy search unavailable: UpstreamError",))
    assert await w.evaluate() == (["service_down"], [])
    alert = (await w.alerts.list(T, open_only=True))[0]
    assert (alert.severity, alert.details["service"], alert.details["failures"]) == ("critical", "policy_search", 3)
    w.clock.tick(6)  # the failures are older than the window
    await w.turn()
    assert await w.evaluate() == ([], ["service_down"]) and await w.open_rules() == {}


async def test_service_down_is_per_service_and_counts_the_shop_too() -> None:
    w = World()
    for _ in range(3):
        await w.turn(errors=("rule checker failed: BACKEND_UNAVAILABLE",))
    for i in range(3):
        await w.turn(
            tool_calls=(ToolCallRecord(request_id=f"g{w.n}{i}", tool="get_order", operation_kind="read", status="error",
                                       error_code="BACKEND_UNAVAILABLE"),)
        )  # fmt: skip
    opened, _ = await w.evaluate()
    assert opened == ["service_down", "service_down"]
    assert sorted(a.details["service"] for a in await w.alerts.list(T, open_only=True)) == ["rule_checker", "shop"]


async def test_action_failing_needs_enough_calls_and_a_high_enough_share() -> None:
    w = World()
    for i in range(9):
        await w.turn(**failing_write(i))
    assert await w.evaluate() == ([], [])  # only 9 calls
    await w.turn(**failing_write(9, "success"))
    assert await w.evaluate() == (["action_failing"], [])  # 9 of 10 failed
    for i in range(30):
        await w.turn(**failing_write(100 + i, "success"))  # now 9 of 40: 22.5% > 20%
    assert await w.evaluate() == ([], [])  # still open, not opened twice
    for i in range(10):
        await w.turn(**failing_write(200 + i, "success"))  # 9 of 50: 18%
    assert await w.evaluate() == ([], ["action_failing"])


async def test_action_failing_ignores_reads() -> None:
    w = World()
    for i in range(20):
        await w.turn(
            tool_calls=(ToolCallRecord(request_id=f"r{i}", tool="get_order", operation_kind="read", status="error"),)
        )
    assert await w.evaluate() == ([], [])


async def test_unverified_result_opens_on_any_and_resolves_after_its_window() -> None:
    w = World()
    await w.turn(escalation_reason=EscalationReason.UNVERIFIED_RESULT)
    assert await w.evaluate() == (["unverified_result"], [])
    assert (await w.alerts.list(T, open_only=True))[0].severity == "critical"
    w.clock.tick(61)
    assert await w.evaluate() == ([], ["unverified_result"])


async def test_action_missing_opens_on_any_and_resolves() -> None:
    w = World()
    await w.turn(escalation_reason=EscalationReason.CAPABILITY_MISSING)
    assert await w.evaluate() == (["action_missing"], [])
    w.clock.tick(61)
    assert await w.evaluate() == ([], ["action_missing"])


async def test_knowledge_gap_rate_over_a_day() -> None:
    w = World()
    for _ in range(8):
        await w.turn(evidence_empty_reason=None, evidence=())  # not a knowledge turn: ignored
    for _ in range(8):
        await w.turn(evidence_empty_reason="no_match")  # asked, nothing found
    for _ in range(2):
        from team_b.domain.trace import EvidenceRef

        await w.turn(evidence=(EvidenceRef(citation="a@v1#s1", document_id="a", version="v1", score=1.0),))
    assert await w.evaluate() == (["knowledge_gap"], [])  # 8 of 10 unanswered
    w.clock.tick(24 * 60 + 1)
    assert await w.evaluate() == ([], ["knowledge_gap"])


async def test_knowledge_gap_for_the_same_question_again_and_again() -> None:
    w = World()
    for _ in range(5):
        await w.turn(customer_message="do you offer a five year warranty on electronics",
                     escalation_reason=EscalationReason.NO_EVIDENCE)  # fmt: skip
    assert await w.evaluate() == (["knowledge_gap"], [])
    alert = (await w.alerts.list(T, open_only=True))[0]
    assert alert.details["key"] == "group" and alert.details["same_question"] == 5
    w.clock.tick(24 * 60 + 1)
    assert await w.evaluate() == ([], ["knowledge_gap"])


async def test_slow_replies_when_the_95th_percentile_is_too_high() -> None:
    w = World()
    for _ in range(20):
        await w.turn(latency_ms=500.0)
    assert await w.evaluate() == ([], [])
    for _ in range(3):
        await w.turn(latency_ms=9000.0)
    assert await w.evaluate() == (["slow_replies"], [])
    w.clock.tick(16)
    for _ in range(6):
        await w.turn(latency_ms=400.0)
    assert await w.evaluate() == ([], ["slow_replies"])


async def test_escalation_spike_against_the_seven_day_share() -> None:
    w = World()
    for _ in range(100):
        await w.turn()  # a calm week: 100 turns
    w.clock.tick(2 * 24 * 60)
    for _ in range(3):
        await w.turn(escalation_reason=EscalationReason.CUSTOMER_REQUEST)
    for _ in range(8):
        await w.turn()
    assert await w.evaluate() == (["escalation_spike"], [])  # 3 of 11 now against 3 of 111 in the week
    w.clock.tick(61)
    for _ in range(12):
        await w.turn()
    assert await w.evaluate() == ([], ["escalation_spike"])


async def test_ai_fallback_when_too_many_ai_turns_fell_back() -> None:
    w = World()
    for _ in range(10):
        await w.turn(nlu_method="llm")
    await w.turn(nlu_method="rules_fallback")
    assert await w.evaluate() == ([], [])  # 1 of 11: 9%
    await w.turn(nlu_method="rules_fallback")
    assert await w.evaluate() == (["ai_fallback"], [])  # 2 of 12: 17%
    w.clock.tick(61)
    for _ in range(6):
        await w.turn(nlu_method="llm")
    assert await w.evaluate() == ([], ["ai_fallback"])


async def test_rule_based_turns_do_not_count_as_ai_turns() -> None:
    w = World()
    for _ in range(20):
        await w.turn(nlu_method="rules")
    assert await w.evaluate() == ([], [])


async def test_queue_backlog_for_an_urgent_case_nobody_took() -> None:
    w = World()
    package = HandoffPackage(
        summary="s", reason=EscalationReason.MANDATORY_RISK, priority="urgent", suggested_next_step="x"
    )
    case = HandoffCase(
        case_id="k1", tenant_id=T, conversation_id="c", package=package, created_at=START, updated_at=START
    )
    await w.cases.add(case)
    w.clock.tick(10)
    assert await w.evaluate() == ([], [])  # inside the 15 minute SLA
    w.clock.tick(6)
    assert await w.evaluate() == (["queue_backlog"], [])
    from team_b.domain.handoff import CaseStatus

    stored = await w.cases.get(T, "k1")
    assert stored is not None
    stored.transition(CaseStatus.CLAIMED, actor="sara", at=w.clock.now())
    await w.cases.save(stored)
    assert await w.evaluate() == ([], ["queue_backlog"])


async def test_a_normal_priority_case_is_not_a_backlog() -> None:
    w = World()
    package = HandoffPackage(
        summary="s", reason=EscalationReason.CUSTOMER_REQUEST, priority="normal", suggested_next_step="x"
    )
    await w.cases.add(
        HandoffCase(case_id="k2", tenant_id=T, conversation_id="c", package=package, created_at=START, updated_at=START)
    )
    w.clock.tick(500)
    assert await w.evaluate() == ([], [])


def test_every_rule_has_a_test_above() -> None:
    assert set(RULES) == {
        "service_down", "action_failing", "unverified_result", "action_missing", "knowledge_gap", "slow_replies",
        "escalation_spike", "ai_fallback", "queue_backlog",
    }  # fmt: skip


# ---- opens once, per-tenant thresholds, acknowledgement ----


async def test_a_condition_opens_one_alert_however_often_it_is_evaluated() -> None:
    w = World()
    await w.turn(escalation_reason=EscalationReason.UNVERIFIED_RESULT)
    for _ in range(5):
        await w.evaluate()
    assert len(await w.alerts.list(T)) == 1


async def test_a_condition_that_comes_back_opens_a_new_alert() -> None:
    w = World()
    await w.turn(escalation_reason=EscalationReason.UNVERIFIED_RESULT)
    await w.evaluate()
    w.clock.tick(61)
    await w.evaluate()  # resolved
    await w.turn(escalation_reason=EscalationReason.UNVERIFIED_RESULT)
    assert await w.evaluate() == (["unverified_result"], [])
    assert len(await w.alerts.list(T)) == 2


async def test_acknowledging_does_not_close_an_alert() -> None:
    w = World()
    await w.turn(escalation_reason=EscalationReason.UNVERIFIED_RESULT)
    await w.evaluate()
    alert = (await w.alerts.list(T))[0]
    alert.acknowledged_by = "sara"
    await w.alerts.save(alert)
    assert await w.evaluate() == ([], [])  # still open, still acknowledged
    stored = await w.alerts.get(T, alert.alert_id)
    assert stored is not None and stored.is_open and stored.acknowledged_by == "sara"


async def test_resolving_records_when_and_keeps_the_acknowledgement() -> None:
    w = World()
    await w.turn(escalation_reason=EscalationReason.CAPABILITY_MISSING)
    await w.evaluate()
    alert = (await w.alerts.list(T))[0]
    alert.acknowledged_by = "sara"
    await w.alerts.save(alert)
    w.clock.tick(61)
    await w.evaluate()
    done = (await w.alerts.list(T))[0]
    assert done.resolved_at == w.clock.now() and done.acknowledged_by == "sara" and not done.is_open


async def test_thresholds_are_per_business() -> None:
    w = World(AlertThresholds(service_down_errors=1, service_down_window_min=1))
    await w.turn(errors=("safety screen unavailable: UpstreamError",))
    assert await w.evaluate() == (["service_down"], [])


async def test_a_business_can_switch_alerts_off() -> None:
    w = World(AlertThresholds(enabled=False))
    await w.turn(escalation_reason=EscalationReason.UNVERIFIED_RESULT)
    assert await w.evaluate() == ([], []) and await w.alerts.list(T) == []


async def test_alerts_of_one_business_are_not_seen_by_another() -> None:
    w = World()
    await w.turn(escalation_reason=EscalationReason.UNVERIFIED_RESULT)
    await w.evaluate()
    assert await w.alerts.list("shop_002") == []
    alert = (await w.alerts.list(T))[0]
    assert await w.alerts.get("shop_002", alert.alert_id) is None


def test_the_thresholds_have_the_agreed_defaults() -> None:
    t = AlertThresholds()
    assert (t.service_down_errors, t.service_down_window_min) == (3, 5)
    assert (t.action_failing_rate, t.action_failing_min_calls, t.action_failing_window_min) == (0.20, 10, 15)
    assert (t.knowledge_gap_rate, t.knowledge_gap_same_question, t.slow_p95_ms, t.slow_window_min) == (
        0.15,
        5,
        6000.0,
        15,
    )
    assert (t.escalation_spike_factor, t.ai_fallback_rate, t.ai_fallback_window_min) == (2.0, 0.10, 60)


def test_the_shop_config_accepts_alert_thresholds(tmp_path: Path) -> None:
    import json

    from team_b.config import Settings
    from team_b.domain.tenant import TenantConfig

    raw = json.loads((Settings().config_dir / "shop_001.json").read_text(encoding="utf-8-sig"))
    raw["alerts"] = {"service_down_errors": 7, "slow_p95_ms": 2500}
    config = TenantConfig.model_validate(raw)
    assert (config.alerts.service_down_errors, config.alerts.slow_p95_ms) == (7, 2500)
    with pytest.raises(ValueError):
        TenantConfig.model_validate({**raw, "alerts": {"service_down_errors": 0}})


# ---- the webhook and the loop ----


async def test_the_webhook_receives_opened_and_resolved_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, Any]] = []

    class Fake:
        def __init__(self, **kw: Any) -> None: ...
        async def __aenter__(self) -> "Fake":
            return self

        async def __aexit__(self, *a: Any) -> None: ...

        async def post(self, url: str, json: dict[str, Any]) -> Any:
            sent.append({"url": url, **json})

            class R:
                def raise_for_status(self) -> None: ...

            return R()

    monkeypatch.setattr("team_b.brain.alerts.httpx.AsyncClient", Fake)
    w = World()
    w.engine = AlertEngine(traces=w.traces, cases=w.cases, alerts=w.alerts, clock=w.clock, tenants=w.tenants,
                           notifier=WebhookNotifier("https://hook.example/x"))  # fmt: skip
    await w.turn(escalation_reason=EscalationReason.UNVERIFIED_RESULT)
    await w.evaluate()
    w.clock.tick(61)
    await w.evaluate()
    assert [(s["event"], s["alert"]["rule"]) for s in sent] == [
        ("opened", "unverified_result"),
        ("resolved", "unverified_result"),
    ]
    assert sent[0]["url"] == "https://hook.example/x" and "customer_message" not in str(sent[0])


async def test_a_failing_webhook_never_loses_the_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    class Boom:
        def __init__(self, **kw: Any) -> None: ...
        async def __aenter__(self) -> "Boom":
            return self

        async def __aexit__(self, *a: Any) -> None: ...

        async def post(self, *a: Any, **k: Any) -> Any:
            raise OSError("down")

    monkeypatch.setattr("team_b.brain.alerts.httpx.AsyncClient", Boom)
    w = World()
    w.engine = AlertEngine(traces=w.traces, cases=w.cases, alerts=w.alerts, clock=w.clock, tenants=w.tenants,
                           notifier=WebhookNotifier("https://hook.example/x"))  # fmt: skip
    await w.turn(escalation_reason=EscalationReason.UNVERIFIED_RESULT)
    assert await w.evaluate() == (["unverified_result"], []) and len(await w.alerts.list(T)) == 1


async def test_the_loop_survives_a_failed_evaluation(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    w = World()
    calls = {"n": 0}
    real = w.engine.evaluate_all

    async def flaky() -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return await real()

    monkeypatch.setattr(w.engine, "evaluate_all", flaky)
    task = asyncio.create_task(alert_loop(w.engine, interval_s=0.01))
    await asyncio.sleep(0.15)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert calls["n"] >= 2


# ---- the stores behave the same ----


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> AlertStore:
    if request.param == "memory":
        return InMemoryAlertStore()
    return SqliteAlertStore(SqliteDatabase(tmp_path / "alerts.sqlite3"))


def make_alert(alert_id: str, rule: str = "service_down", key: str = "shop", tenant: str = T, at: int = 0) -> Alert:
    return Alert(alert_id=alert_id, tenant_id=tenant, rule=rule, severity="critical",
                 opened_at=START + timedelta(minutes=at), details={"key": key})  # fmt: skip


async def test_store_adds_gets_lists_newest_first(store: AlertStore) -> None:
    await store.add(make_alert("a", key="x", at=0))
    await store.add(make_alert("b", key="y", at=5))
    assert [a.alert_id for a in await store.list(T)] == ["b", "a"]
    assert (await store.get(T, "a")) == make_alert("a", key="x", at=0)
    assert await store.get(T, "zzz") is None and await store.get("other", "a") is None


async def test_store_allows_one_open_alert_per_rule_and_key(store: AlertStore) -> None:
    await store.add(make_alert("a"))
    with pytest.raises(AlreadyExistsError):
        await store.add(make_alert("b"))  # the same rule and key, still open
    await store.add(make_alert("c", key="other"))
    first = await store.get(T, "a")
    assert first is not None
    first.resolved_at = START
    await store.save(first)
    await store.add(make_alert("d"))  # the first is resolved: a new one may open
    with pytest.raises(AlreadyExistsError):
        await store.add(make_alert("d", key="elsewhere"))  # the same id


async def test_store_save_updates_and_unknown_is_not_found(store: AlertStore) -> None:
    await store.add(make_alert("a"))
    alert = await store.get(T, "a")
    assert alert is not None
    alert.acknowledged_by = "sara"
    await store.save(alert)
    again = await store.get(T, "a")
    assert again is not None and again.acknowledged_by == "sara"
    with pytest.raises(NotFoundError):
        await store.save(make_alert("nope"))


async def test_store_open_only_and_limit(store: AlertStore) -> None:
    for i in range(5):
        await store.add(make_alert(f"a{i}", key=str(i), at=i))
    done = await store.get(T, "a0")
    assert done is not None
    done.resolved_at = START
    await store.save(done)
    assert len(await store.list(T, open_only=True)) == 4 and len(await store.list(T, limit=2)) == 2


async def test_store_is_per_tenant(store: AlertStore) -> None:
    await store.add(make_alert("a"))
    await store.add(make_alert("a", tenant="shop_002"))  # the same id for another business is fine
    assert len(await store.list(T)) == 1 and len(await store.list("shop_002")) == 1
