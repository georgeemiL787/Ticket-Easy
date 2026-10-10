"""The agent's vocabulary: the moves it may propose, the plan it keeps, and the record it leaves on the trace.

In agent mode a turn is a short loop. The orchestrator (an AI model, later) proposes ONE move at a time from this menu;
code validates the move and decides whether it runs. A move is only a proposal: it never calls a tool by itself, never
clears a safety flag and never skips a gate (CLAUDE.md rule 1). The menu:

- LookUp(tool, arguments)        read something from the shop (an order, a customer's orders);
- SearchPolicy(query)            find policy passages for a question;
- ProposeAction(intent, arguments)  start an action (cancel, return, refund ...): it still goes through every gate;
- AskCustomer(question_key, slot, options)  ask for a missing detail or a choice;
- HandOver(reason, tried)        give the conversation to a person, with what was tried first;
- Reply(plan)                    answer: the plan says what the reply decides and which verified facts it may hold.

Every move carries a one-line `why` for the trace. The plan (1 to 5 steps) is saved on the session between turns.
"""

from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter

from team_b.domain.base import FrozenModel
from team_b.domain.decision import Decision, EscalationReason

AgentMode = Literal["on", "off", "shadow"]  # shadow: the agent only watches (built later); until then like off
MAX_PLAN_STEPS = 5
MAX_ARGUMENT_CHARS = 2000  # a longer value is not a tool argument, it is an attempt to smuggle text through


class ReplyPlan(FrozenModel):
    """What a reply may say (filled in with the reply writer): the decision, the template and the verified facts."""

    decision: Decision
    intent: str = Field(default="", max_length=200)  # what the reply is for, in one line ("ask for the order number")
    free: bool = False  # an off-topic message: the writer answers the person and steers back (code sets it)
    reply_key: str | None = None  # a template of data/locales (the fallback text, and what code sends for now)
    facts: dict[str, str] = Field(default_factory=dict)  # name -> value, only values taken from verified data
    must_ask: str | None = None  # a question the reply must ask, e.g. a yes/no
    citations: list[str] = Field(default_factory=list)


class LookUp(FrozenModel):
    kind: Literal["look_up"] = "look_up"
    why: str = Field(default="", max_length=300)
    tool: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)


class SearchPolicy(FrozenModel):
    kind: Literal["search_policy"] = "search_policy"
    why: str = Field(default="", max_length=300)
    query: str = Field(min_length=1, max_length=MAX_ARGUMENT_CHARS)


class ProposeAction(FrozenModel):
    kind: Literal["propose_action"] = "propose_action"
    why: str = Field(default="", max_length=300)
    intent: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)


class AskCustomer(FrozenModel):
    kind: Literal["ask_customer"] = "ask_customer"
    why: str = Field(default="", max_length=300)
    question_key: str = Field(min_length=1, max_length=100)  # a template key, e.g. ask_order_id
    slot: str | None = None  # the detail the answer will fill
    options: list[str] = Field(default_factory=list)  # a choice offered, e.g. order ids


class HandOver(FrozenModel):
    kind: Literal["hand_over"] = "hand_over"
    why: str = Field(default="", max_length=300)
    reason: EscalationReason
    tried: list[str] = Field(default_factory=list)  # what was tried first, in plain words


class Reply(FrozenModel):
    kind: Literal["reply"] = "reply"
    why: str = Field(default="", max_length=300)
    plan: ReplyPlan


AnyMove = LookUp | SearchPolicy | ProposeAction | AskCustomer | HandOver | Reply
Move = Annotated[AnyMove, Field(discriminator="kind")]
MOVE_ADAPTER: TypeAdapter[AnyMove] = TypeAdapter(Move)
ENDS_THE_TURN = (AskCustomer, HandOver, Reply)  # these end the turn; the others are worked and the loop goes on


def parse_move(data: Any) -> AnyMove:
    """A move from the model's JSON (or an object): raises pydantic.ValidationError when it is not on the menu."""
    return MOVE_ADAPTER.validate_python(data)


class PlanStep(FrozenModel):
    text: str = Field(min_length=1, max_length=300)
    status: Literal["open", "waiting", "done", "dropped"] = "open"  # waiting: for the customer's yes to an action


class AgentPlan(FrozenModel):
    """The steps the agent keeps for the customer's message(s). Saved on the session; `revision` counts the changes."""

    steps: list[PlanStep] = Field(default_factory=list, max_length=MAX_PLAN_STEPS)
    revision: int = Field(default=0, ge=0)

    @property
    def open_steps(self) -> list[PlanStep]:
        return [s for s in self.steps if s.status == "open"]


class MoveProposal(FrozenModel):
    """What the orchestrator answers in one ask: the plan as it now stands (None: unchanged) and the next move."""

    plan: AgentPlan | None = None
    move: AnyMove


class ToolMenuEntry(FrozenModel):
    """A shop tool the agent may name in a LookUp: what it does, whether it only reads, and what it takes."""

    name: str
    description: str = ""
    kind: Literal["read", "write"]
    arguments: dict[str, Any] = Field(default_factory=dict)  # the tool's input schema


class ActionMenuEntry(FrozenModel):
    """An action the agent may propose by intent name (code maps it to the tool and runs every gate)."""

    intent: str
    description: str = ""


class OrderBrief(FrozenModel):
    """An order the agent has looked up for this verified customer (only what it needs to decide)."""

    order_id: str
    status: str
    delivered_at: str | None = None
    expected_delivery_date: str | None = None
    total: float | None = None
    days_late: int | None = None


class PassageBrief(FrozenModel):
    id: str
    text: str


class AgentView(FrozenModel):
    """What the move picker sees. Built by code; personal values (phone, email, card, address) are already hidden."""

    message: str
    turn_id: str = ""
    locale: str = "en"  # the reply style: en, ar or arabizi
    history: list[str] = Field(default_factory=list)  # the last few messages, "customer: ..." / "agent: ..."
    language: str | None = None  # what the reader understood ...
    intents: list[str] = Field(default_factory=list)
    entities: dict[str, str] = Field(default_factory=dict)
    frustration: str = "low"
    identity_verified: bool = False
    customer_id: str | None = None  # the verified customer's id (the argument list_customer_orders takes)
    orders: list[OrderBrief] = Field(default_factory=list)  # looked up and confirmed to be the customer's own
    known_order_ids: list[str] = Field(default_factory=list)  # ids the customer wrote or a tool returned
    passages: list[PassageBrief] = Field(default_factory=list)  # policy passages found this turn
    no_evidence_count: int = 0  # policy questions in a row that the documents could not answer
    tools: list[ToolMenuEntry] = Field(default_factory=list)
    actions: list[ActionMenuEntry] = Field(default_factory=list)
    plan: AgentPlan | None = None
    moves: list["AgentStepRecord"] = Field(default_factory=list)  # the moves already made this turn, with results
    refused: str | None = None  # why the last proposed move was refused, so the next one can differ
    pending: str | None = None  # what the agent was waiting for from the customer last turn (slot:<name>, ...)
    told_already: list[str] = Field(default_factory=list)  # replies already settled this turn (the customer's yes)


class CheckRecord(FrozenModel):
    """One check that code made on a move before it ran: passed or not, and why."""

    name: str
    passed: bool
    reason: str = ""


class AgentStepRecord(FrozenModel):
    """One step of the loop, for the trace. Personal values are redacted before it is stored."""

    move: dict[str, Any]
    why: str = ""
    checks: list[CheckRecord] = Field(default_factory=list)
    result: str = ""  # a short, redacted summary of what happened
    duration_ms: float = Field(default=0.0, ge=0.0)
    fallback_reason: str | None = None  # set on the record that made the turn fall back to today's stages

    @property
    def refused(self) -> bool:
        return any(not c.passed for c in self.checks)


AgentView.model_rebuild()
