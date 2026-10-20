"""The orchestrator: an AI model that plans the message and proposes the next move (job "orchestrator").

LLMMovePicker implements the MovePicker port. Each ask it receives an AgentView (built by code, personal values hidden:
no phone, email, card or address), shows it to the model as sections (customer message, recent history, what the reader
understood, what is verified, the plan, the moves already made with their results, the tools), and parses the answer
{"plan": [...], "move": {...}} into a MoveProposal. The plan and the move come from the one model call, so a simple
message costs a single ask. Tool results are shown inside <tool_result> tags as data. An answer that cannot be used is
asked for once more with the problem named; after that the picker raises, and the loop falls back to the usual stages.

The model only proposes. Whether a move runs is decided by code (agent_loop.check_move, agent_reads).
"""

import json
from typing import Any

from pydantic import ValidationError

from agent_service.brain.llm_nlu import PromptTemplate
from agent_service.domain.agent import AgentPlan, AgentStepRecord, AgentView, MoveProposal, PlanStep, parse_move
from agent_service.observability import get_logger
from agent_service.ports import LLMClient

log = get_logger(__name__)
PROMPT = "orchestrator_v1"
SCHEMA_HINT = {"plan": [{"text": "step", "status": "open|done|dropped"}], "move": {"kind": "reply"}}
MAX_RESULT_SHOWN = 1200


class UnusableAnswer(ValueError):
    """The model's answer is not a plan and a move of the menu."""


def _plan_of(raw: Any) -> AgentPlan | None:
    """The plan the model gave: a list of steps (text, or {text, status}); None when it gave none."""
    if raw is None or raw == []:
        return None
    if not isinstance(raw, list):
        raise UnusableAnswer("plan must be a list of steps")
    steps = [PlanStep(text=item) if isinstance(item, str) else PlanStep.model_validate(item) for item in raw]
    return AgentPlan(steps=steps)  # at most five (AgentPlan checks); the revision is set when the loop saves it


def parse_answer(data: dict[str, Any]) -> MoveProposal:
    """The model's JSON as a proposal, or UnusableAnswer / pydantic's ValidationError saying what is wrong."""
    if not isinstance(data.get("move"), dict):
        raise UnusableAnswer('the answer needs a "move" object')
    return MoveProposal(plan=_plan_of(data.get("plan")), move=parse_move(data["move"]))


def _move_line(number: int, record: AgentStepRecord) -> str:
    shown = json.dumps(record.move, ensure_ascii=False)
    refused = [c.reason for c in record.checks if not c.passed]
    outcome = f"REFUSED by code: {'; '.join(refused)}" if refused else "worked"
    result = record.result[:MAX_RESULT_SHOWN]
    return f"{number}. {shown} -> {outcome}\n<tool_result>{result}</tool_result>"


class LLMMovePicker:
    def __init__(self, llm: LLMClient, prompt: PromptTemplate | None = None) -> None:
        self._llm = llm
        self._prompt = prompt or PromptTemplate.load(PROMPT)

    @property
    def prompt_version(self) -> str:
        return self._prompt.version

    def user_prompt(self, view: AgentView) -> str:
        """The sections of the ask. Everything in them was built by code and has personal values hidden."""
        reading = {
            "language": view.language,
            "intents": view.intents,
            "details": view.entities,
            "frustration": view.frustration,
        }
        verified = {
            "customer_verified": view.identity_verified,
            "customer_id": view.customer_id,
            "orders_read": [o.model_dump(exclude_none=True) for o in view.orders],
            "order_numbers_known": view.known_order_ids,
            "policy_passages_found": [p.model_dump() for p in view.passages],
            "no_evidence_count": view.no_evidence_count,
            "questions_asked": view.questions_asked,
            "frustration_strikes": view.frustration_strikes,
            "waiting_for": view.pending,
            "active_request": view.active_request,
            "requests_so_far": view.requests_so_far,
            "details_the_customer_already_gave": view.details_given,
        }
        if view.told_already:
            verified["already_told_to_the_customer_this_turn"] = view.told_already
        if view.refused:
            verified["last_move_refused_because"] = view.refused
        tools = [
            {"name": t.name, "kind": t.kind, "description": t.description, "arguments": t.arguments} for t in view.tools
        ]
        actions = [{"intent": a.intent, "description": a.description} for a in view.actions]
        plan = json.dumps(view.plan.model_dump(), ensure_ascii=False) if view.plan else "(no plan yet)"
        moves = "\n".join(_move_line(i, r) for i, r in enumerate(view.moves, start=1)) or "(none yet)"
        return self._prompt.render_user(
            locale=view.locale,
            message=view.message,
            history="\n".join(view.history) or "(no earlier messages)",
            reading=json.dumps(reading, ensure_ascii=False),
            verified=json.dumps(verified, ensure_ascii=False),
            plan=plan,
            moves=moves,
            tools=json.dumps({"read_and_write_tools": tools, "action_intents": actions}, ensure_ascii=False),
        )

    async def next_move(self, view: AgentView) -> MoveProposal:
        user = self.user_prompt(view)
        problem = ""
        for _ in range(2):  # one repair attempt, then the loop falls back
            ask = user if not problem else f"{user}\n\nYour last answer was not usable ({problem}). Answer again."
            data = await self._llm.complete_json(system=self._prompt.system, user=ask, schema_hint=SCHEMA_HINT)
            try:
                return parse_answer(data)
            except (UnusableAnswer, ValidationError, ValueError) as exc:
                problem = str(exc).splitlines()[0][:200]
                log.warning("orchestrator_answer_unusable", problem=problem)
        raise UnusableAnswer(problem)
