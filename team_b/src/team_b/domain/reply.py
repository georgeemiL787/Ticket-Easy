"""What the brain sends back to the customer for one message."""

from pydantic import Field

from team_b.domain.base import FrozenModel
from team_b.domain.decision import Decision
from team_b.domain.understanding import Locale


class AgentReply(FrozenModel):
    request_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    text: str
    locale: Locale
    decision: Decision
    citations: tuple[str, ...] = ()
    trace_id: str = Field(min_length=1)
    handoff_case_id: str | None = None
    awaiting: str | None = None  # what the agent waits for next: a detail, a yes/no, a human
