"""The tenant configuration bundle: one versioned, immutable-once-published description of a business.

A bundle embeds the existing TenantConfig unchanged and adds what the onboarding flow produces: the business profile,
the knowledge that was ingested, the approved guardrails and the tools. Secrets are never part of a bundle (credentials
stay encrypted in the tools service; the bundle holds ids only). Its JSON Schema is
contracts/schemas/TenantBundle.schema.json.
"""

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from agent_service.domain.base import FrozenModel
from agent_service.domain.tenant import EscalationConfig, TenantConfig
from agent_service.domain.understanding import Locale

BUNDLE_SCHEMA_VERSION = "1.0"
BundleStatus = Literal["draft", "published", "active", "retired"]
Tone = Literal["friendly", "formal", "neutral"]
ToolPermission = Literal["read", "confirm", "human_only"]
ToolRisk = Literal["low", "medium", "high"]
SECRET_NAME = re.compile(r"(secret|password|passwd|token|api[_-]?key|credential|authorization|private[_-]?key)", re.I)


class BundleProfile(FrozenModel):
    display_name: str = Field(min_length=1)
    locales: tuple[Locale, ...] = (Locale.EN,)
    default_locale: Locale = Locale.EN
    tone: Tone = "friendly"

    @model_validator(mode="after")
    def _default_is_offered(self) -> Self:
        if self.default_locale not in self.locales:
            raise ValueError("profile.default_locale must be one of profile.locales")
        return self


class BundleDocument(FrozenModel):
    document_id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    status: Literal["uploaded", "parsing", "indexed", "failed"] = "indexed"


class BundleKnowledge(FrozenModel):
    provider: Literal["team_a", "standin"] = "standin"
    index_tenant: str | None = None  # the tenant id the knowledge service indexes this business under
    built_at: datetime | None = None
    documents: tuple[BundleDocument, ...] = ()


class ViolationBehaviour(FrozenModel):
    """What happens when a guardrail is hit. Fixed by the gates in brain/actions.py; stored so the bundle shows it."""

    deny: Literal["refuse"] = "refuse"
    require_human: Literal["handoff"] = "handoff"
    unavailable: Literal["handoff"] = "handoff"


class BundleGuardrails(FrozenModel):
    ruleset_version: str | None = None  # the knowledge service's version id of the approved rules
    ruleset_sha256: str | None = None
    approved_rule_ids: tuple[str, ...] = ()
    violation: ViolationBehaviour = Field(default_factory=ViolationBehaviour)


class ToolBindingRef(FrozenModel):
    """Which tools-service publication serves a tool. Ids only, no credentials."""

    business_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    environments: tuple[Literal["sandbox", "live_demo", "production"], ...] = Field(default=("sandbox",), min_length=1)
    arguments: dict[str, str] = Field(default_factory=dict)  # Team B argument name -> tools-service input name
    fields: dict[str, str] = Field(default_factory=dict)  # Team B output field -> tools-service output name
    reference_field: str | None = None
    echo: tuple[str, ...] = ()
    constants: dict[str, str] = Field(default_factory=dict)
    spec: dict[str, Any] | None = None  # the ToolSpec the brain sees, for a tool the built-in shop does not declare


class BundleTool(FrozenModel):
    name: str = Field(min_length=1)
    source: Literal["team_c", "standin"] = "standin"
    binding: ToolBindingRef | None = None
    permission: ToolPermission = "read"
    risk: ToolRisk = "low"

    @model_validator(mode="after")
    def _team_c_tools_are_bound(self) -> Self:
        if self.source == "team_c" and self.binding is None:
            raise ValueError(f"tools.{self.name}: a tool served by the tools service needs a binding")
        return self


def _secret_keys(value: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            here = f"{path}.{key}" if path else str(key)
            if SECRET_NAME.search(str(key)):
                found.append(here)
            found += _secret_keys(item, here)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found += _secret_keys(item, f"{path}[{index}]")
    return found


def secret_like_keys(data: Any) -> list[str]:
    """Paths of every field in this JSON-like value whose name looks like a secret (empty when it is clean)."""
    return _secret_keys(data)


class TenantBundle(FrozenModel):
    schema_version: Literal["1.0"] = BUNDLE_SCHEMA_VERSION  # type: ignore[assignment]
    tenant_id: str = Field(pattern=r"^[a-z0-9_]{1,64}$")
    version: int = Field(ge=1)
    status: BundleStatus = "draft"
    created_at: datetime
    created_by: str = Field(min_length=1)
    parent_version: int | None = Field(default=None, ge=1)
    content_sha256: str = Field(default="", pattern=r"^([0-9a-f]{64})?$")  # computed from the content when left out
    config: TenantConfig
    profile: BundleProfile
    knowledge: BundleKnowledge = Field(default_factory=BundleKnowledge)
    guardrails: BundleGuardrails = Field(default_factory=BundleGuardrails)
    tools: tuple[BundleTool, ...] = ()
    escalation: EscalationConfig | None = None  # replaces config.escalation when set
    identities: dict[str, str] = Field(
        default_factory=dict
    )  # verified customer id -> test account name (names, not credentials)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.config.tenant_id != self.tenant_id:
            raise ValueError("config.tenant_id must equal tenant_id")
        names = [tool.name for tool in self.tools]
        if len(set(names)) != len(names):
            raise ValueError("tools: names must be unique")
        allowed = self.config.permissions.allowed_tools
        if self.tools:
            unknown = [n for n in allowed if n != "*" and n not in names]
            if unknown:
                raise ValueError(f"config.permissions.allowed_tools names tools the bundle does not list: {unknown}")
        for tool in self.tools:
            if tool.permission == "human_only" and tool.name in allowed:
                raise ValueError(f"tools.{tool.name}: a human-only tool must not be in allowed_tools")
        if self.parent_version is not None and self.parent_version >= self.version:
            raise ValueError("parent_version must be lower than version")
        leaked = secret_like_keys(self.content())
        if leaked:
            raise ValueError(f"a bundle never holds secrets; remove these fields: {leaked}")
        digest = self.compute_sha256()
        if not self.content_sha256:
            object.__setattr__(self, "content_sha256", digest)  # frozen model: fill the computed hash once
        elif self.content_sha256 != digest:
            raise ValueError("content_sha256 does not match the content")
        return self

    def content(self) -> dict[str, Any]:
        """The part that defines behaviour (no version, status or author): what the hash covers."""
        data = self.model_dump(mode="json")
        for key in (
            "version",
            "status",
            "created_at",
            "created_by",
            "parent_version",
            "content_sha256",
            "schema_version",
        ):
            data.pop(key, None)
        return data

    def compute_sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.content(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def effective_config(self) -> TenantConfig:
        """The configuration the brain runs with: the embedded one, with the profile and escalation applied."""
        update: dict[str, Any] = {
            "display_name": self.profile.display_name,
            "default_locale": self.profile.default_locale,
            "bundle_version": self.version,
            "bundle_sha256": self.content_sha256,
        }
        if self.escalation is not None:
            update["escalation"] = self.escalation
        return self.config.model_copy(update=update)

    def with_status(self, status: BundleStatus) -> Self:
        return self.model_copy(update={"status": status})
