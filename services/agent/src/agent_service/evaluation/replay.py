"""Replay: run a past conversation again with other settings and look at the two runs side by side.

The customer's messages of a stored conversation are said again, in order, to a FRESH copy of the service: the simulated
shop, the simulated policy search, rules and safety screen, and memory stores that nobody else sees. Nothing here can
reach the real shop or the real stores, and a replay is never saved as a conversation. The conversation is played twice:
"before" with the settings as they are, "after" with the changes (--set KEY=VALUE, the same names as the environment:
TEAM_B_LLM_MODEL_PLANNER, TEAM_B_AGENT_MODE, TEAM_B_LLM_REWRITE, ...). reports/replay_<id>_<date>.md shows each
message with both runs' reply, decision and moves, and marks where they differ.

The messages come from the stored session (the words as the customer wrote them); when the session is gone (retention)
the stored traces are used instead, and those have phone numbers and the like hidden, so a replay of them may ask again.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from agent_service.config import Settings
from agent_service.container import Container, build_container
from agent_service.domain.agent import AgentStepRecord
from agent_service.ports import Planner

MAX_TURNS = 40
TENANT_DEFAULT_TODAY = date(2026, 9, 28)  # the demo shop's orders are dated against this day


class ReplayMove(BaseModel):
    kind: str
    summary: str
    refused: bool


class ReplayTurn(BaseModel):
    said: str
    reply: str
    decision: str
    escalation: str | None
    awaiting: str | None
    agent_mode: str | None
    fallback_reason: str | None
    moves: list[ReplayMove]


class ReplayRun(BaseModel):
    label: str
    turns: list[ReplayTurn]
    writes: int  # changes the (simulated service) shop saw in this run


class ReplayResult(BaseModel):
    conversation_id: str
    tenant_id: str
    messages_from: str  # "session" (the customer's own words) or "traces" (personal values hidden)
    changes: dict[str, str]
    before: ReplayRun
    after: ReplayRun
    differing_turns: list[int]  # 1-based


def apply_changes(base: Settings, changes: Mapping[str, str]) -> Settings:
    """The settings with `changes` (environment-style names) put on top. A job's settings are changed field by field."""
    if not changes:
        return base
    changed = Settings.from_env(dict(changes))
    update: dict[str, Any] = {name: getattr(changed, name) for name in changed.model_fields_set if name != "roles"}
    roles = dict(base.roles)
    for role, new in changed.roles.items():
        own = {f: getattr(new, f) for f in new.model_fields_set}
        roles[role] = roles[role].model_copy(update=own) if role in roles else new
    update["roles"] = roles
    if "llm" not in changed.model_fields_set and (changed.roles or "llm_model" in update) and base.llm == "none":
        update["llm"] = "openai"
    return base.model_copy(update=update)


def parse_changes(items: list[str]) -> dict[str, str]:
    """KEY=VALUE pairs from the command line."""
    found: dict[str, str] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"{item!r} is not KEY=VALUE")
        found[key.strip()] = value.strip()
    return found


async def customer_messages(container: Container, tenant_id: str, conversation_id: str) -> tuple[list[str], str]:
    """What the customer said, oldest first, and where it was read from."""
    session = await container.sessions.load(tenant_id, conversation_id)
    if session is not None:
        said = [m.text for m in session.history if m.role == "customer"]
        if said:
            return said, "session"
    traces = await container.traces.for_conversation(tenant_id, conversation_id)
    return [t.customer_message for t in traces if t.kind == "customer_turn" and t.customer_message], "traces"


def moves_of(steps: tuple[AgentStepRecord, ...]) -> list[ReplayMove]:
    return [
        ReplayMove(kind=str(s.move.get("kind", "invalid")), summary=s.result[:200], refused=s.refused) for s in steps
    ]


async def play(
    settings: Settings,
    tenant_id: str,
    messages: list[str],
    label: str,
    *,
    planner: Planner | None = None,
    today: date | None = None,
) -> ReplayRun:
    """The messages said to a fresh service built from `settings` (simulated shop, memory stores)."""
    fresh = settings.model_copy(
        update={
            "mode": "standin",
            "store": "memory",
            "fixed_today": today or settings.fixed_today or TENANT_DEFAULT_TODAY,
        }
    )
    container = build_container(fresh)
    assert container.orchestrator is not None and container.shop is not None
    if planner is not None:
        container.orchestrator.set_agent(mode="on", planner=planner)
    chat = f"replay-{label}"
    turns: list[ReplayTurn] = []
    for said in messages[:MAX_TURNS]:
        reply = await container.orchestrator.handle_turn(tenant_id, chat, said)
        trace = await container.traces.get(tenant_id, reply.trace_id)
        turns.append(
            ReplayTurn(
                said=said,
                reply=reply.text,
                decision=reply.decision.value,
                escalation=trace.escalation_reason.value if trace and trace.escalation_reason else None,
                awaiting=reply.awaiting,
                agent_mode=trace.agent_mode if trace else None,
                fallback_reason=trace.fallback_reason if trace else None,
                moves=moves_of(trace.agent_steps) if trace else [],
            )
        )
    kinds = {t.name: t.operation_kind for t in await container.capabilities.list_tools(tenant_id)}
    writes = sum(1 for e in container.shop.audit_log(tenant_id) if kinds.get(e.tool, "write") != "read" and e.applied)
    return ReplayRun(label=label, turns=turns, writes=writes)


def differs(a: ReplayTurn, b: ReplayTurn) -> bool:
    return (a.reply, a.decision, a.escalation) != (b.reply, b.decision, b.escalation) or [m.kind for m in a.moves] != [
        m.kind for m in b.moves
    ]


@dataclass
class Pickers:
    """Move pickers for the two runs (tests give scripted ones; None: whatever the settings build)."""

    before: Planner | None = None
    after: Planner | None = None
    extra: dict[str, Any] = field(default_factory=dict)


async def replay(
    live: Container,
    base: Settings,
    tenant_id: str,
    conversation_id: str,
    changes: Mapping[str, str],
    *,
    pickers: Pickers | None = None,
    today: date | None = None,
) -> ReplayResult:
    """Say the conversation again with and without `changes`. `live` is only read, to get the customer's messages."""
    messages, source = await customer_messages(live, tenant_id, conversation_id)
    if not messages:
        raise LookupError(f"conversation {conversation_id} has no customer messages")
    chosen = pickers or Pickers()
    before = await play(base, tenant_id, messages, "before", planner=chosen.before, today=today)
    after = await play(apply_changes(base, changes), tenant_id, messages, "after", planner=chosen.after, today=today)
    differing = [i for i, (a, b) in enumerate(zip(before.turns, after.turns, strict=True), start=1) if differs(a, b)]
    return ReplayResult(
        conversation_id=conversation_id,
        tenant_id=tenant_id,
        messages_from=source,
        changes=dict(changes),
        before=before,
        after=after,
        differing_turns=differing,
    )


# ---- the report ----


def _cell(turn: ReplayTurn) -> str:
    moves = ", ".join(f"{m.kind}{' (refused)' if m.refused else ''}" for m in turn.moves) or "no moves"
    extra = f" · fell back: {turn.fallback_reason}" if turn.fallback_reason else ""
    reason = f" ({turn.escalation})" if turn.escalation else ""
    text = turn.reply.replace("\n", " ").replace("|", "\\|")
    return f"**{turn.decision}**{reason}{extra}<br>{text}<br><sub>{moves}</sub>"


def render(result: ReplayResult, when: date) -> str:
    lines = [
        f"# Replay of {result.conversation_id} · {when.isoformat()}",
        "",
        f"- shop: {result.tenant_id}; the customer's messages come from the {result.messages_from}",
        f"- changes for the second run: {', '.join(f'{k}={v}' for k, v in result.changes.items()) or 'none'}",
        f"- messages that differ: {', '.join(map(str, result.differing_turns)) or 'none'}"
        f" (of {len(result.before.turns)})",
        f"- changes made in the simulated shop: before {result.before.writes}, after {result.after.writes}",
        "",
        "| # | the customer said | before | after | |",
        "|---|---|---|---|---|",
    ]
    for i, (a, b) in enumerate(zip(result.before.turns, result.after.turns, strict=True), start=1):
        mark = "differs" if i in result.differing_turns else "same"
        lines.append(f"| {i} | {a.said.replace('|', chr(92) + '|')} | {_cell(a)} | {_cell(b)} | {mark} |")
    return "\n".join(lines) + "\n"


def write_report(result: ReplayResult, directory: Path, when: date) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"replay_{result.conversation_id}_{when.isoformat()}.md"
    path.write_text(render(result, when), encoding="utf-8")
    return path
