"""Shared base class and type aliases for the plug contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

OperationKind = Literal["read", "create", "update", "delete"]
RiskLevel = Literal["low", "medium", "high"]


class PlugModel(BaseModel):
    """Anything received from a plug: frozen, and unknown fields are ignored so a plug may add fields."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class RequestModel(BaseModel):
    """Anything we send to a plug: frozen, and unknown fields are rejected so typos fail loudly."""

    model_config = ConfigDict(extra="forbid", frozen=True)
