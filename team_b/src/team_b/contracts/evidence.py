"""Evidence formats. Mirror team_a/contracts/schemas (Passage, RetrievalResult, PastTicketResult, RiskAssessment)."""

from datetime import date
from typing import Literal, Self

from pydantic import AliasChoices, Field, model_validator

from team_b.contracts.base import PlugModel

EmptyReason = Literal["below_threshold", "no_documents"]


class Passage(PlugModel):
    """One quoted passage of the shop policy. passage_id is the citation, e.g. return_policy@v2#s2."""

    passage_id: str = Field(min_length=1)
    document_id: str
    version: str
    section: str
    language: str  # Team A sends ar | en | mixed. Kept open so a new value from a plug does not break us.
    text: str
    score: float

    @property
    def citation(self) -> str:
        """Team A also sends a citation field, and it always equals passage_id."""
        return self.passage_id


class RetrievalResult(PlugModel):
    """Answer of the policy search. Exactly one of passages / empty_reason is filled."""

    request_id: str = ""
    tenant_id: str = ""
    query: str = ""
    passages: tuple[Passage, ...] = ()
    empty_reason: EmptyReason | None = None
    retrieval_mode: Literal["hybrid", "keyword_only"] = "hybrid"

    @model_validator(mode="after")
    def _passages_xor_empty_reason(self) -> Self:
        if self.passages and self.empty_reason is not None:
            raise ValueError("empty_reason must be None when passages exist")
        if not self.passages and self.empty_reason is None:
            raise ValueError("an empty result must say why (empty_reason)")
        return self


class PastTicket(PlugModel):
    ticket_id: str
    category: str
    customer_message: str
    resolution: str
    created_at: date
    score: float
    citation: str


class PastTicketResult(PlugModel):
    request_id: str = ""
    tenant_id: str = ""
    query: str = ""
    tickets: tuple[PastTicket, ...] = ()
    empty_reason: EmptyReason | None = None

    @model_validator(mode="after")
    def _tickets_xor_empty_reason(self) -> Self:
        if self.tickets and self.empty_reason is not None:
            raise ValueError("empty_reason must be None when tickets exist")
        if not self.tickets and self.empty_reason is None:
            raise ValueError("an empty result must say why (empty_reason)")
        return self


class RiskAssessment(PlugModel):
    """Answer of the safety screen. Team A names the flagged field mandatory_escalation; both names are accepted."""

    flagged: bool = Field(validation_alias=AliasChoices("flagged", "mandatory_escalation"))
    categories: tuple[str, ...] = ()
    matched_terms: tuple[str, ...] = ()
    method: str = "keywords"
    llm_rationale: str | None = None
