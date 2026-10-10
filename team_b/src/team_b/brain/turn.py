"""The shared state of one turn: what the stages read and write, and the small result type a stage can decide with."""

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from team_b.brain.knowledge_agent import KnowledgeAgent
from team_b.brain.knowledge_llm import KnowledgeAssistant
from team_b.brain.language import LOCALE_FOR
from team_b.brain.nlu import NLU
from team_b.brain.registry import CapabilityRegistry
from team_b.brain.reviewer import Reviewer
from team_b.brain.rewrite import Rewriter
from team_b.brain.smalltalk import SmallTalk
from team_b.brain.summarizer import HistorySummarizer
from team_b.brain.writer import Writer
from team_b.contracts.evidence import Passage, RiskAssessment
from team_b.domain.agent import AgentMode, AgentPlan, AgentStepRecord, ReplyPlan
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.session import SessionState
from team_b.domain.tenant import TenantConfig
from team_b.domain.trace import EvidenceRef, PolicyRecord, ToolCallRecord, TraceStep
from team_b.domain.understanding import Locale, NLUResult
from team_b.ports import (
    CapabilityClient,
    CaseStore,
    Clock,
    EvidenceProvider,
    LLMClient,
    MovePicker,
    PolicyGate,
    SessionStore,
    TraceStore,
)

# after one of these the next queued intent may run
COMPLETED = frozenset({Decision.ANSWER, Decision.EXECUTE, Decision.REFUSE})
MAX_QUEUED_RUNS = 3


@dataclass(frozen=True)
class Step:
    """What a stage decided: the outcome, why, and what to say (a template key plus its values)."""

    decision: Decision
    reason: str  # for the trace, in plain words
    reply_key: str  # which template to say (see data/locales and brain/composer.py)
    values: Mapping[str, str] = field(default_factory=dict)
    citations: tuple[str, ...] = ()
    escalation: EscalationReason | None = None  # required when decision is HANDOFF
    awaiting: str | None = None  # what the agent waits for next: slot:<name>, confirmation, detail, human
    silent: bool = False  # say nothing (a customer message that only goes to the human who owns the chat)
    passages: tuple[Passage, ...] = ()  # policy passages quoted verbatim after the reply; never sent to the AI model
    knowledge: bool = False  # a policy answer: the trace must then show the evidence it came from
    text: str | None = None  # a policy answer worded by the AI model (fact-checked); replaces the template
    plan: ReplyPlan | None = None  # the reply plan the orchestrator chose; else it is read off the key and values
    escalation_kind: Literal["forced", "chosen"] = "forced"  # a handoff decided by code, or chosen by the orchestrator
    tried: tuple[str, ...] = ()  # what the orchestrator says it tried before handing over (plain words)

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
    ai_events: list[tuple[str, str, str]] = field(default_factory=list)  # (job, kind, detail)
    agent_passages: list[Passage] = field(default_factory=list)  # policy passages the agent found this turn
    agent_policy_text: str = ""  # the knowledge agent's checked wording of the policy answer found this turn
    agent_policy_citations: tuple[str, ...] = ()  # the passages that wording relies on
    agent_outcomes: list[Step] = field(default_factory=list)  # refusals and done/failed actions not yet told
    agent_wrote: bool = False  # the agent loop had the shop change something this turn
    shadow_plan: AgentPlan | None = None  # shadow mode: what the agent would have planned
    shadow_move: AgentStepRecord | None = None  # shadow mode: its first move
    shadow_error: str | None = None  # shadow mode: why it could not be asked
    resumed: Step | None = None  # the answer to the customer's yes, kept while the agent goes on with its plan
    agent_listing: list[dict[str, str]] = field(default_factory=list)  # the customer's own orders, if listed this turn
    agent_mode: str | None = None  # how the agent was configured for this turn
    agent_steps: list[AgentStepRecord] = field(default_factory=list)  # the loop's moves this turn
    fallback_reason: str | None = None  # why the agent loop gave up (the usual stages ran instead)
    override_notice: bool = (
        False  # the message tells the agent to ignore its rules: the reply starts with a fixed notice
    )
    apologise: bool = False  # the customer is very frustrated for the first time: the reply starts with an apology
    steps: list[TraceStep] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    policy: list[PolicyRecord] = field(default_factory=list)  # every rule-checker answer used this turn
    proposal_ids: list[str] = field(default_factory=list)  # action proposals created or moved this turn
    versions: dict[str, str] = field(default_factory=dict)  # prompt versions used this turn
    errors: list[str] = field(default_factory=list)
    evidence: list[EvidenceRef] = field(default_factory=list)  # policy passages retrieved this turn
    evidence_empty_reason: str | None = None


def locale_of(ctx: TurnContext) -> Locale:
    """The reply style: the conversation language mapped through the one locale table, else the tenant default."""
    language = ctx.session.language
    return LOCALE_FOR[language] if language is not None else Locale(ctx.tenant.default_locale.value)


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
    rewriter: Rewriter | None = None  # optional AI rewording of low-stakes replies (the usual flow, agent off)
    writer: Writer | None = None  # optional AI writer of replies from a reply plan (agent mode)
    reviewer: Reviewer | None = None  # optional AI reviewer: replies and hand-overs (stricter only)
    smalltalk: SmallTalk | None = None  # optional AI writer of small-talk replies (greetings, check-ins)
    llm: LLMClient | None = None  # optional AI model, used for the handoff summary
    knowledge: KnowledgeAssistant | None = None  # optional AI model that steers the policy search and words the answer
    knowledge_agent: KnowledgeAgent | None = None  # optional AI agent: searches (twice if needed), answers or not found
    registry: CapabilityRegistry | None = None  # the cached list of published shop tools
    policy: PolicyGate | None = None  # the rule checker; without it no action can be checked, so none runs
    move_picker: MovePicker | None = None  # the orchestrator; without it the agent is unavailable
    agent_mode: AgentMode = "on"  # the service default; a tenant may set its own
    agent_max_moves: int = 5
    agent_turn_timeout_s: float = 20.0
    max_queued_runs: int = MAX_QUEUED_RUNS  # queued requests that may run after the first one, in a single turn
