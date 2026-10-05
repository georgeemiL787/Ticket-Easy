"""Everything shop-specific lives in config/tenants/<tenant>.json. These models validate that file."""

import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, ValidationError, field_validator, model_validator

from team_b.contracts.base import OperationKind, RiskLevel
from team_b.domain.base import FrozenModel
from team_b.domain.understanding import Locale

IntentKind = Literal["knowledge", "lookup", "action", "handoff", "smalltalk"]


class TenantConfigError(Exception):
    """A tenant file is missing, unreadable or invalid."""


class UnknownTenantError(KeyError):
    """No configuration is loaded for this tenant id."""


class IdentityConfig(FrozenModel):
    verify_tool: str = Field(min_length=1)  # tool that checks the customer, e.g. order number plus phone
    required_slots: tuple[str, ...] = ("order_id", "phone")
    max_attempts: int = Field(default=2, ge=1)


class EscalationConfig(FrozenModel):
    max_tool_failures: int = Field(default=2, ge=1)
    max_clarifications: int = Field(default=2, ge=1)
    min_intent_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    escalate_on_deny: bool = True  # a no from the rule checker always goes to a human
    escalate_on_high_frustration: bool = True


class PermissionsConfig(FrozenModel):
    """Which tools the agent may use at all. The default allows nothing (fail closed)."""

    allowed_tools: tuple[str, ...] = ()
    max_risk: RiskLevel = "medium"
    confirm_operation_kinds: tuple[OperationKind, ...] = ("create", "update", "delete")


class IntentSpec(FrozenModel):
    kind: IntentKind
    description: str = ""
    examples: tuple[str, ...] = ()
    lookup_tool: str | None = None
    action_tool: str | None = None
    required_slots: tuple[str, ...] = ()
    argument_map: dict[str, str] = Field(default_factory=dict)  # tool argument name -> slot or fact name
    knowledge_query: str | None = None  # hint added to the policy search for this intent
    knowledge_when: str | None = None  # condition (a named fact) under which a policy quote is also shown
    labels: dict[str, str] = Field(default_factory=dict)  # locale (en/ar/arabizi) -> short name used in questions

    @model_validator(mode="after")
    def _kind_has_its_tool(self) -> Self:
        if self.kind == "action" and not self.action_tool:
            raise ValueError("an action intent needs action_tool")
        if self.kind == "lookup" and not self.lookup_tool:
            raise ValueError("a lookup intent needs lookup_tool")
        return self


class TenantConfig(FrozenModel):
    tenant_id: str = Field(pattern=r"^[a-z0-9_]{1,64}$")
    display_name: str = Field(min_length=1)
    default_locale: Locale = Locale.EN
    history_max_turns: int = Field(default=12, ge=1)
    order_id_pattern: str = Field(min_length=1)  # regular expression, e.g. NS-\d{4,6}
    order_id_prefix: str = ""
    identity: IdentityConfig
    escalation: EscalationConfig = Field(default_factory=EscalationConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    intents: dict[str, IntentSpec] = Field(default_factory=dict)
    conflicting_intents: tuple[tuple[str, str], ...] = ()  # pairs that cannot both be wanted: ask which one

    @field_validator("order_id_pattern")
    @classmethod
    def _pattern_compiles(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"order_id_pattern is not a valid regular expression: {exc}") from exc
        return value


class TenantRegistry:
    """All businesses the service knows, loaded from config/tenants/*.json."""

    def __init__(self, tenants: Mapping[str, TenantConfig]) -> None:
        self._tenants = dict(tenants)

    @classmethod
    def from_dir(cls, path: Path) -> Self:
        if not path.is_dir():
            raise TenantConfigError(f"tenant directory not found: {path}")
        tenants: dict[str, TenantConfig] = {}
        for file in sorted(path.glob("*.json")):
            try:
                config = TenantConfig.model_validate_json(file.read_text(encoding="utf-8-sig"))
            except (ValidationError, OSError) as exc:
                raise TenantConfigError(f"{file.name}: {exc}") from exc
            if config.tenant_id != file.stem:
                raise TenantConfigError(f"{file.name}: tenant_id {config.tenant_id!r} must match the file name")
            tenants[config.tenant_id] = config
        return cls(tenants)

    def get(self, tenant_id: str) -> TenantConfig:
        try:
            return self._tenants[tenant_id]
        except KeyError:
            raise UnknownTenantError(tenant_id) from None

    def tenant_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._tenants))

    def __contains__(self, tenant_id: object) -> bool:
        return tenant_id in self._tenants

    def __len__(self) -> int:
        return len(self._tenants)

    def __iter__(self) -> Iterator[TenantConfig]:
        return iter(self._tenants.values())
