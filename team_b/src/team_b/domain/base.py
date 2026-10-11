"""Base classes for the brain own models. Unknown fields are rejected so typos fail loudly."""

from pydantic import BaseModel, ConfigDict


class FrozenModel(BaseModel):
    """Immutable value object."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class MutableModel(BaseModel):
    """State that changes over time (session, proposal, case). Assignments are re-validated."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)
