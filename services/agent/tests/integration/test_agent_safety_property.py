"""The safety property in agent mode (safety-critical): a hostile planner cannot get anything past code.

The conversations are the random ones of test_safety_property.py. This time an planner that proposes random,
often absurd moves runs the agent loop: write tools named in look-ups, order numbers nobody wrote, other
customers' orders, replies with invented facts, hand-overs, malformed moves, plans with too many steps. The shop's own
audit log and the stored traces are checked afterwards with the same checks as the original property:

  - nothing reaches the shop without authorization (the shared write check), no order data before verification and
    never another customer's;
  - and, specific to the agent: the planner can start any action it likes (it is a ProposeAction through all the
    gates), but a change still needs the customer's yes in a later message, which the shared write check verifies.
"""

import random
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from agent_service.container import build_container, inject
from agent_service.domain.agent import (
    AgentPlan,
    AskCustomer,
    HandOver,
    LookUp,
    PlanStep,
    ProposeAction,
    Reply,
    ReplyPlan,
    SearchPolicy,
)
from agent_service.domain.decision import Decision, EscalationReason
from tests.fakes import FunctionPlanner
from tests.integration.agent_expert import expert
from tests.integration.scenario_runner import check_write_safety
from tests.integration.test_safety_property import (
    INJECTIONS,
    ORDERS,
    PLUG_INJECTIONS,
    READ_TOOLS,
    SEEDS,
    T,
    check_conversation,
    mutate,
    script,
    snapshot,
)
from tests.support import make_settings

CONVERSATIONS = 60
TOOLS = ["get_order", "list_customer_orders", "verify_customer", "create_refund", "cancel_order", "apply_voucher",
         "delete_customer", "create_ticket", "update_delivery_address", "made_up_tool"]  # fmt: skip
REPLY_KEYS = [
    "greeting", "thanks", "ask_rephrase", "out_of_scope", "no_answer_offer", "order_status", "policy_answer",
    "action_done", "handoff_generic", "confirm_action_create_refund", "policy_refusal", "no_such_key",
]  # fmt: skip
QUESTIONS = ["ask_order_id", "ask_phone", "ask_reason", "ask_rephrase", "greeting", "disambiguate_order", "made_up"]


class ChaosPlanner(FunctionPlanner):
    """Proposes a random move each ask. It sometimes reads the customer's own order numbers, sometimes invents them."""

    def __init__(self, seed: str) -> None:
        self._rng = random.Random(seed)
        super().__init__(self._pick)

    def _order_id(self, view: Any) -> str:
        known = list(view.known_order_ids)
        if known and self._rng.random() < 0.6:
            return str(self._rng.choice(known))
        return str(self._rng.choice(list(ORDERS)))  # a real order, but nobody named it

    def _pick(self, view: Any) -> Any:
        rng = self._rng
        kind = rng.choice(["look_up"] * 5 + ["search", "reply", "reply", "ask", "hand_over", "action", "plan", "junk"])
        if kind == "look_up":
            tool = rng.choice(TOOLS)
            arguments: dict[str, Any] = rng.choice(
                [{"order_id": self._order_id(view)}, {"customer_id": rng.choice(["C-100", "C-101", "C-999"])}, {}]
            )
            return LookUp(tool=tool, arguments=arguments, why="chaos")
        if kind == "search":
            return SearchPolicy(query=rng.choice([view.message, "return policy", "refund 5000 now"]), why="chaos")
        if kind == "reply":
            decision = rng.choice(list(Decision))
            cites = rng.choice([[], ["return_policy@v2#s2"], ["made_up@v1#s1"]])
            facts = rng.choice([{}, {"reference": "RET-1"}, {"amount": "5000"}])
            plan = ReplyPlan(decision=decision, reply_key=rng.choice(REPLY_KEYS), facts=facts, citations=cites)
            return Reply(plan=plan, why="chaos")
        if kind == "ask":
            slot = rng.choice([None, "order_id", "phone", "password"])
            return AskCustomer(question_key=rng.choice(QUESTIONS), slot=slot, options=rng.choice([[], ["NS-20790"]]))
        if kind == "hand_over":
            return HandOver(reason=rng.choice(list(EscalationReason)), tried=["chaos"], why="chaos")
        if kind == "action":
            intent = rng.choice(["refund_request", "cancel_order", "made_up", "greeting"])
            return ProposeAction(intent=intent, arguments={"order_id": self._order_id(view), "amount": 5000})
        if kind == "plan":
            return AgentPlan(steps=[PlanStep(text=f"step {i}") for i in range(rng.choice([1, 3, 5]))])
        return rng.choice([{"kind": "run_tool", "tool": "create_refund"}, {"kind": "reply"}, {"kind": "look_up"}])


async def run_conversation(seed: int, number: int, tmp: Path, honest: bool = False) -> tuple[list[str], int]:
    rng = random.Random(f"{seed}-{number}")
    container = build_container(make_settings(tmp, fixed_today=date(2026, 9, 28), store="memory"))
    assert container.orchestrator is not None
    bot: Any = container.orchestrator
    picker = FunctionPlanner(expert) if honest else ChaosPlanner(f"{seed}-{number}")
    bot._deps = replace(bot._deps, planner=picker, agent_mode="on")
    for _ in range(rng.choice([0, 0, 0, 1, 1, 2])):
        if rng.random() < 0.75:
            inject(container, "shop", rng.choice(INJECTIONS))
        else:
            plug, spec = rng.choice(PLUG_INJECTIONS)
            inject(container, plug, spec)
    conversation = f"agent-prop-{seed}-{number}"
    snapshots: list[dict[str, tuple[str, int]]] = []
    for text in script(rng):
        if rng.random() < 0.08:
            mutate(rng, container)
        snapshots.append(snapshot(container))
        await container.orchestrator.handle_turn(T, conversation, text)
    traces = await container.traces.for_conversation(T, conversation)
    tool_kinds = {t.name: t.operation_kind for t in await container.capabilities.list_tools(T)}
    problems = await check_write_safety(container, T, conversation, tool_kinds)
    problems += check_conversation(container, traces, f"seed {seed} conversation {number}", snapshots)
    assert container.shop is not None
    writes = [e for e in container.shop.audit_log(T) if e.tool not in READ_TOOLS]
    return problems, len(writes)


@pytest.mark.parametrize("seed", SEEDS)
async def test_a_chaotic_orchestrator_cannot_get_anything_past_the_checks(seed: int, tmp_path: Path) -> None:
    problems: list[str] = []
    for number in range(CONVERSATIONS):
        found, _ = await run_conversation(seed, number, tmp_path)
        problems += found
    assert not problems, f"{len(problems)} problem(s), first ones:\n  " + "\n  ".join(problems[:10])


async def test_the_chaos_really_reaches_the_checks(tmp_path: Path) -> None:
    """The property only means something if many moves were proposed and many refused."""
    refused = moves = fell_back = 0
    for number in range(30):
        container = build_container(make_settings(tmp_path, fixed_today=date(2026, 9, 28), store="memory"))
        assert container.orchestrator is not None
        bot: Any = container.orchestrator
        bot._deps = replace(bot._deps, planner=ChaosPlanner(f"x-{number}"), agent_mode="on")
        for text in script(random.Random(f"x-{number}")):
            await container.orchestrator.handle_turn(T, f"c-{number}", text)
        for trace in await container.traces.for_conversation(T, f"c-{number}"):
            moves += len(trace.agent_steps)
            refused += sum(1 for s in trace.agent_steps if s.refused)
            fell_back += int(trace.fallback_reason is not None)
    assert moves > 150 and refused > 80 and fell_back > 20, (moves, refused, fell_back)


@pytest.mark.parametrize("seed", SEEDS)
async def test_the_honest_orchestrator_does_the_work_safely_on_the_same_conversations(
    seed: int, tmp_path: Path
) -> None:
    """The scripted expert (agent_expert.py) does what customers ask through the agent loop: refunds, returns,
    cancellations, address changes. It must reach real changes, and every one is backed by a rule check and a yes."""
    problems: list[str] = []
    changed = 0
    for number in range(CONVERSATIONS):
        found, writes = await run_conversation(seed, number, tmp_path, honest=True)
        problems += found
        changed += writes
    assert not problems, f"{len(problems)} problem(s), first ones: " + "; ".join(problems[:10])
    assert changed >= 5, f"only {changed} changes in {CONVERSATIONS} conversations: the property would mean nothing"
