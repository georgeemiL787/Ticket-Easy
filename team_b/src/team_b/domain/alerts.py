"""Alerts: a warning for managers that something needs attention (service down, actions failing, a backlog...)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from team_b.domain.base import MutableModel

AlertSeverity = Literal["info", "warning", "critical"]


class Alert(MutableModel):
    """One alert. It opens once when its rule's condition starts and is resolved when the condition clears."""

    alert_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    rule: str = Field(min_length=1)  # which alert rule raised it, e.g. service_down
    severity: AlertSeverity
    opened_at: datetime
    resolved_at: datetime | None = None
    acknowledged_by: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None
