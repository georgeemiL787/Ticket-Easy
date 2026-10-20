"""The tiny JSON Schema checker moved to agent_service.contracts.json_schema
(the core checks agent moves with it too)."""

from agent_service.contracts.json_schema import validate

__all__ = ["validate"]
