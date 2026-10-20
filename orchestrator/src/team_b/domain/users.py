"""People who sign in to the inbox and the dashboard: who they are, their role, and the businesses they may see."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from team_b.domain.base import FrozenModel

Role = Literal["agent", "manager", "admin"]
ROLE_RANK: dict[str, int] = {"agent": 1, "manager": 2, "admin": 3}


class User(FrozenModel):
    """A person. An admin sees every business; a manager or agent only the ones listed in tenants."""

    user_id: str = Field(min_length=1)
    email: str = Field(min_length=3, max_length=254)
    display_name: str = Field(min_length=1, max_length=80)  # unique: the inbox records it as the actor
    role: Role
    tenants: tuple[str, ...] = ()
    active: bool = True
    created_at: datetime

    def can_see(self, tenant_id: str) -> bool:
        return self.active and (self.role == "admin" or tenant_id in self.tenants)

    def at_least(self, role: str) -> bool:
        return self.active and ROLE_RANK[self.role] >= ROLE_RANK[role]
