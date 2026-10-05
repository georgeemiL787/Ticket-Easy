"""The shared state of one turn: what the stages read and write, and the small result type a stage can decide with."""

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

from team_b.brain.nlu import NLU
from team_b.brain.summarizer import HistorySummarizer
from team_b.contracts.evidence import RiskAssessment
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.session import SessionState
from team_b.domain.tenant import TenantConfig
from team_b.domain.trace import TraceStep
from team_b.domain.understanding import NLUResult
from team_b.ports import CapabilityClient, CaseStore, Clock, EvidenceProvider, SessionStore, TraceStore

# after one of these the next queued intent may run
COMPLETED = frozenset({Decision.ANSWER, Decision.EXECUTE, Decision.REFUSE})
MAX_QUEUED_RUNS = 3


@dataclass(frozen=True)
class Step:
    """What a stage decided: the outcome, why, and what to say (a template key plus its values)."""

    decision: Decision
    reason: str  # for the trace, in plain words
    reply_key: str  # which template to say (see brain/templates.py)
    values: Mapping[str, str] = field(default_factory=dict)
    citations: tuple[str, ...] = ()
    escalation: EscalationReason | None = None  # required when decision is HANDOFF
    awaiting: str | None = None  # what the agent waits for next: slot:<name>, confirmation, detail, human
    silent: bool = False  # say nothing (a customer message that only goes to the human who owns the chat)

    def __post_init__(self) -> None:
        if self.decision is Decision.HANDOFF and self.escalation is None:
            raise ValueError("a handoff step needs an escalation reason")


@dataclass(frozen=True)
class PlannedIntent:
    name: str
    kind: str  # knowledge | lookup | action | smalltalk | handoff


@dataclass
class TurnContext:
    deps: "Deps"
    tenant: TenantConfig
    session: SessionState
    text: str
    now: datetime
    request_id: str
    trace_id: str
    started: float = field(default_factory=time.perf_counter)
    understanding: NLUResult | None = None
    risk: RiskAssessment | None = None
    risk_unavailable: bool = False
    human_owned: bool = False  # a human already has this conversation
    plan: PlannedIntent | None = None
    step: Step | None = None  # the decision, once a stage has made it
    earlier: list[Step] = field(default_factory=list)  # completed steps whose replies come before the final one
    handoff_case_id: str | None = None
    steps: list[TraceStep] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


Handler = Callable[[TurnContext, PlannedIntent], Awaitable[Step]]
StageFn = Callable[[TurnContext], Awaitable[str]]


@dataclass(frozen=True)
class Deps:
    """Everything the stages may use. Built once by the container."""

    clock: Clock
    sessions: SessionStore
    traces: TraceStore
    cases: CaseStore
    evidence: EvidenceProvider | None
    nlu: NLU
    summarizer: HistorySummarizer
    handlers: Mapping[str, Handler]  # by intent kind
    capabilities: CapabilityClient | None = None  # the shop tools; None means none are known
