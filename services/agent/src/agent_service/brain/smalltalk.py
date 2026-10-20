"""Small talk written by the AI model, with a code check that keeps it to small talk.

When the customer's whole message is a greeting, a check-in ("how are you"), thanks or a goodbye, the fixed greeting
template can feel robotic. With an AI model configured the SmallTalkWriter answers the person ("I'm doing well, thank
you for asking!") and offers help, in their language and style. Code decides whether the text may be sent: it must add
no number, date, id or link (check_grounded against the template and the shop's name), may not mention an order, an
amount, a policy, a promise or a colleague, and must be short. On any failure or model error the template is sent.
"""

import re
from typing import Literal, NamedTuple, Protocol

from agent_service.brain.composer import check_grounded, promises_handover
from agent_service.brain.llm_nlu import PromptTemplate
from agent_service.brain.redaction import redact
from agent_service.brain.rewrite import REGISTER
from agent_service.domain.understanding import Locale
from agent_service.observability import get_logger
from agent_service.ports import LLMClient

log = get_logger(__name__)
PROMPT = "smalltalk_v1"
SCHEMA_HINT = {"text": "the reply"}
MAX_CHARS = 300
MAX_PROBLEMS_SHOWN = 4
# Small talk says nothing about the business: no order, money, policy or promise words in any style.
OFF_LIMITS = re.compile(
    r"\borders?\b|refund|\breturns?\b|polic(?:y|ies)|promis|guarantee|voucher|deliver|shipp|\bprices?\b|discount"
    r"|اوردر|أوردر|طلبك|طلباتك|استرداد|استرجاع|سياس|ضمان|وعد|كوبون|شحن|توصيل|فلوس|سعر"
    r"|refound|ordery|talabak|siyasa|wa3d|shohn|tawsil|felous|flous",
    re.IGNORECASE,
)


class SmallTalkResult(NamedTuple):
    text: str  # what to send: the model's reply if it passed, else the template
    status: Literal["used", "rejected", "failed"]
    detail: str


class SmallTalk(Protocol):
    async def write(self, message: str, history: str, locale: Locale, shop: str, template: str) -> SmallTalkResult: ...

    @property
    def prompt_version(self) -> str: ...


class SmallTalkWriter:
    def __init__(self, llm: LLMClient, prompt: PromptTemplate | None = None) -> None:
        self._llm = llm
        self._prompt = prompt or PromptTemplate.load(PROMPT)

    @property
    def prompt_version(self) -> str:
        return self._prompt.version

    async def write(self, message: str, history: str, locale: Locale, shop: str, template: str) -> SmallTalkResult:
        """The model's small-talk reply if it passes every check, else `template` unchanged, with the reason."""
        register = REGISTER[locale]
        system = self._prompt.system.replace("{{shop}}", shop).replace("{{register}}", register)
        user = self._prompt.render_user(
            register=register,
            history=redact(history) or "(no earlier messages)",
            message=redact(message),
            text=template,
        )
        try:
            data = await self._llm.complete_json(system=system, user=user, schema_hint=SCHEMA_HINT)
        except Exception as exc:  # any model failure means: send the template
            log.warning("smalltalk_failed", reason=type(exc).__name__)
            return SmallTalkResult(template, "failed", f"the model failed ({type(exc).__name__})")
        candidate = data.get("text")
        if not isinstance(candidate, str) or not candidate.strip():
            return SmallTalkResult(template, "failed", "the model answered without text")
        candidate = candidate.strip()
        problems = problems_with(candidate, template, shop)
        if problems:
            return SmallTalkResult(template, "rejected", "; ".join(problems[:MAX_PROBLEMS_SHOWN]))
        return SmallTalkResult(candidate, "used", "written")


# "How are you today?" promises nothing; only a word about another day ("tomorrow") could pass for a commitment.
TODAY_WORDS = frozenset({"today", "اليوم", "النهارده", "النهاردة", "enharda", "elnaharda"})


def problems_with(candidate: str, template: str, shop: str) -> list[str]:
    """Why a small-talk reply must not be sent; empty means it may."""
    problems = [
        p for p in check_grounded(candidate, [template, shop]) if p.removeprefix("new time word ") not in TODAY_WORDS
    ]
    if len(candidate) > MAX_CHARS:
        problems.append("too long for small talk")
    if (found := OFF_LIMITS.search(candidate)) is not None:
        problems.append(f"talks about the business ({found.group(0).lower()})")
    if promises_handover(candidate):
        problems.append("mentions a colleague")
    return problems
