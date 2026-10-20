"""Escalation in agent mode: what code forces at once, and what the orchestrator may choose only after trying.

SAFETY-CRITICAL. A handoff is always safe for the customer, so the point of these rules is the other direction: a
person's time is not spent on a case the agent could still solve, and a briefing never states a fact that code did not
establish.

FORCED (the reason is decided by code; the orchestrator is never asked, and it may not choose these reasons itself):
  a flagged safety screen (mandatory_risk), the customer asks for a person (customer_request), a human already owns
  the chat, require_human from the rules or the reviewer (approval_required), an unverified write
  (unverified_result), another customer's order (ownership_mismatch, also an order number the shop does not know twice
  in a row), identity failed after the maximum attempts (identity_failed), a repeated policy deny (policy_denied).
  They are made by the front stages and the gates of brain/agent_actions.py and brain/agent_reads.py, with no move of
  the orchestrator in between. Their steps carry escalation_kind "forced".

CHOSEN (HandOver move, escalation_kind "chosen"): the orchestrator hands over by itself, but code checks that the agent
really tried first, with the limits in the tenant's escalation settings:
  high_frustration       the customer is still very frustrated after frustration_attempts attempt(s) to solve it
                         (the first very frustrated message gets an apology and the request goes on);
  no_evidence            a policy question the documents could not answer twice (the knowledge agent already
                         retried inside each search) and policy_questions clarifying question(s) asked in between;
  dependency_unavailable / repeated_tool_failure
                         a temporary outage that stayed after outage_retries retry(ies);
  low_confidence         the request is still unclear after max_clarifications questions.
Reasons a person must judge anyway (unsupported, capability_missing, customer_request phrased in a way the word lists
missed) are always allowed: handing over early is the safe direction.

The list of what was tried goes with the case: what the orchestrator said it tried, plus what the trace shows
(its moves and their outcomes, the questions asked, the failures counted), in plain words.
"""

from team_b.brain.redaction import redact
from team_b.brain.turn import Step, TurnContext
from team_b.domain.agent import AgentStepRecord, HandOver
from team_b.domain.decision import Decision, EscalationReason

R = EscalationReason
# reasons only code can establish: the orchestrator may not state them
FORCED_ONLY = frozenset(
    {
        R.MANDATORY_RISK,
        R.OWNERSHIP_MISMATCH,
        R.IDENTITY_FAILED,
        R.APPROVAL_REQUIRED,
        R.POLICY_DENIED,
        R.UNVERIFIED_RESULT,
    }
)
# reasons the orchestrator may choose only after trying (see the module text)
TRIED_FIRST = frozenset(
    {R.HIGH_FRUSTRATION, R.NO_EVIDENCE, R.DEPENDENCY_UNAVAILABLE, R.REPEATED_TOOL_FAILURE, R.LOW_CONFIDENCE}
)
MAX_TRIED_ITEMS = 12
SHORT = 140
DEPENDENCY_ERRORS = ("policy search", "the shop tool list", "order list", "safety screen", "rule checker")


def note_turn_start(ctx: TurnContext) -> None:
    """Count a very frustrated message as the usual stages do: the first gets an apology and the request goes on, a
    second in a row can be handed over (by the orchestrator, after this attempt). Any other message resets the count."""
    session = ctx.session
    level = ctx.understanding.frustration if ctx.understanding else "low"
    if level != "high":
        session.frustration_strikes = 0
        return
    if not ctx.tenant.escalation.escalate_on_high_frustration:
        return
    session.frustration_strikes += 1
    if session.frustration_strikes == 1:
        ctx.apologise = True
        session.clarifications = 0  # an apology and one more try: this message is not another unanswered question


def note_turn_end(ctx: TurnContext, step: Step) -> None:
    """A turn that answered, did or refused something starts the count of unanswered questions again."""
    if step.decision in (Decision.ANSWER, Decision.EXECUTE, Decision.REFUSE):
        ctx.session.agent_questions = 0


def _outage_attempts(ctx: TurnContext) -> int:
    """Failed attempts to reach a system: reads of the shop that failed in a row, plus other dependency errors."""
    seen = sum(1 for e in ctx.errors if e.lower().startswith(DEPENDENCY_ERRORS))
    return ctx.session.tool_failures + seen


def handover_problem(ctx: TurnContext, move: HandOver) -> str | None:
    """Why the orchestrator may not hand over yet (said back to it), or None when it may."""
    session, cfg = ctx.session, ctx.tenant.escalation
    reason = move.reason
    if reason in FORCED_ONLY:
        return f"{reason.value} is decided by the system, not chosen: ask the customer or answer instead"
    if reason is R.HIGH_FRUSTRATION:
        high = ctx.understanding is not None and ctx.understanding.frustration == "high"
        if not high or session.frustration_strikes <= cfg.frustration_attempts:
            return (
                "the customer is not still very frustrated after an attempt to solve it: apologise and solve it first"
            )
    elif reason is R.NO_EVIDENCE:
        if session.no_evidence_count < 2 or session.agent_questions < cfg.policy_questions:
            return "search the policy again and ask the customer one clarifying question before handing over"
    elif reason in (R.DEPENDENCY_UNAVAILABLE, R.REPEATED_TOOL_FAILURE):
        if _outage_attempts(ctx) <= cfg.outage_retries:
            return "retry once before handing over because of an outage"
    elif reason is R.LOW_CONFIDENCE and session.agent_questions < cfg.max_clarifications:
        return f"ask the customer {cfg.max_clarifications} question(s) about the request before handing over"
    return None


def _move_words(record: AgentStepRecord) -> str | None:
    if record.result.startswith("refused"):
        return None  # a refused move did nothing
    move = record.move
    kind = move.get("kind")
    result = record.result[:SHORT]
    if kind == "look_up":
        return f"read {move.get('tool')} in the shop: {result}"
    if kind == "search_policy":
        return f"searched the policy: {result}"
    if kind == "ask_customer":
        return f"asked the customer ({move.get('question_key')})"
    if kind == "propose_action":
        return f"proposed {move.get('intent')}: {result}"
    if kind == "reply":
        return f"replied: {result}"
    return None


def tried_in_words(ctx: TurnContext, step: Step) -> tuple[str, ...]:
    """What was tried before this handoff, in plain words: the orchestrator's own list, the moves of this turn and
    the counts the session holds. Only the orchestrator's own list comes from a model (and is redacted)."""
    session = ctx.session
    items: list[str] = list(step.tried)
    items += [w for w in (_move_words(r) for r in ctx.agent_steps) if w]
    if session.no_evidence_count:
        items.append(f"the policy search found nothing {session.no_evidence_count} time(s) in a row")
    if session.agent_questions or session.clarifications:
        items.append(f"asked the customer {max(session.agent_questions, session.clarifications)} question(s)")
    if session.tool_failures:
        items.append(f"{session.tool_failures} read(s) from the shop failed in a row")
    if session.order_misses:
        items.append(f"the shop did not know the order number {session.order_misses} time(s)")
    if session.frustration_strikes:
        items.append(
            "apologised to the customer and tried to solve it"
            if session.frustration_strikes == 1
            else f"the customer stayed very frustrated for {session.frustration_strikes} messages in a row"
        )
    if session.denied_actions:
        items.append(f"the policy refused: {', '.join(session.denied_actions)}")
    cleaned = [redact(i).strip()[: SHORT * 2] for i in items if i and i.strip()]
    return tuple(dict.fromkeys(cleaned))[:MAX_TRIED_ITEMS]
