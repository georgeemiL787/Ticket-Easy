"""The tenant bundle registry: the TenantRegistry the core reads, with versioned bundles layered over the tenant files.

For a business with an active bundle, the bundle's configuration is what the core runs with; for every other business
it is the file in config/tenants/. Activating a version swaps the configuration atomically, so the next customer turn
uses it without a restart; rolling back activates the parent version. Published bundles are immutable: a change is a new
draft (a copy with a higher version number).
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from agent_service.domain.bundle import (
    BundleGuardrails,
    BundleKnowledge,
    BundleProfile,
    BundleTool,
    TenantBundle,
    ToolBindingRef,
)
from agent_service.domain.tenant import TenantConfig, TenantRegistry, UnknownTenantError
from agent_service.ports import AlreadyExistsError, BundleStore, Clock, NotFoundError, StoreError

if TYPE_CHECKING:
    from agent_service.adapters.team_c_tools import TenantBinding

Listener = Callable[[TenantBundle | None, str], Awaitable[None]]  # (new active bundle or None, tenant id)
PublishCheck = Callable[[TenantBundle], Awaitable[list[str]]]  # problems that stop a publication (empty: fine)


class BundleError(StoreError):
    """A bundle operation was refused; the message says why."""


class BundleRegistry(TenantRegistry):
    def __init__(self, files: TenantRegistry, store: BundleStore, clock: Clock) -> None:
        super().__init__({config.tenant_id: config for config in files})
        self._files: dict[str, TenantConfig] = {config.tenant_id: config for config in files}
        self._active: dict[str, TenantBundle] = {}
        self._drafts: dict[str, TenantBundle] = {}  # businesses that are only being set up: known, but not live
        self.store = store
        self._clock = clock
        self._locks: dict[str, asyncio.Lock] = {}
        self._listeners: list[Listener] = []
        self._publish_checks: list[PublishCheck] = []

    # ---- the registry the core reads ----

    def add_listener(self, listener: Listener) -> None:
        """Called after a tenant's active bundle changes (the tools binding reloads there)."""
        self._listeners.append(listener)

    def add_publish_check(self, check: PublishCheck) -> None:
        """A check that may stop a bundle from being published (for example: guardrails not approved)."""
        self._publish_checks.append(check)

    def active_bundle(self, tenant_id: str) -> TenantBundle | None:
        return self._active.get(tenant_id)

    def file_config(self, tenant_id: str) -> TenantConfig | None:
        return self._files.get(tenant_id)

    @property
    def drafts(self) -> frozenset[str]:
        """Businesses that exist only as a draft: staff can test them, customers get TENANT_NOT_LIVE."""
        return frozenset(self._drafts)

    def draft_bundle(self, tenant_id: str) -> TenantBundle | None:
        return self._drafts.get(tenant_id)

    async def load(self) -> None:
        """Take the active bundle of every business from the store (at start-up), and the latest draft of the businesses
        that have no live configuration yet."""
        for bundle in await self.store.all_active():
            self._apply(bundle)
            await self._notify(bundle, bundle.tenant_id)
        for bundle in await self.store.all_latest():
            if (
                bundle.tenant_id not in self._active
                and bundle.tenant_id not in self._files
                and bundle.status != "retired"
            ):
                await self._use_draft(bundle)

    async def _use_draft(self, bundle: TenantBundle) -> None:
        """A business not live yet runs on its latest draft, for staff only (the preview flag marks its traces)."""
        tenants = dict(self._tenants)
        tenants[bundle.tenant_id] = bundle.effective_config().model_copy(update={"preview": True})
        self._tenants = tenants
        self._drafts[bundle.tenant_id] = bundle
        await self._notify(bundle, bundle.tenant_id)

    def _apply(self, bundle: TenantBundle | None, tenant_id: str | None = None) -> None:
        tenant_id = bundle.tenant_id if bundle is not None else tenant_id
        assert tenant_id is not None
        tenants = dict(self._tenants)  # replaced as a whole: a turn in progress never sees a half-made change
        if bundle is not None:
            self._active[tenant_id] = bundle
            self._drafts.pop(tenant_id, None)
            tenants[tenant_id] = bundle.effective_config()
        else:
            self._active.pop(tenant_id, None)
            if tenant_id in self._files:
                tenants[tenant_id] = self._files[tenant_id]
            else:
                tenants.pop(tenant_id, None)
        self._tenants = tenants

    async def _notify(self, bundle: TenantBundle | None, tenant_id: str) -> None:
        for listener in self._listeners:
            await listener(bundle, tenant_id)

    def _lock(self, tenant_id: str) -> asyncio.Lock:
        return self._locks.setdefault(tenant_id, asyncio.Lock())

    # ---- operations ----

    async def create_draft(
        self,
        tenant_id: str,
        *,
        created_by: str,
        config: TenantConfig | None = None,
        profile: BundleProfile | None = None,
        knowledge: BundleKnowledge | None = None,
        guardrails: BundleGuardrails | None = None,
        tools: tuple[BundleTool, ...] | None = None,
        identities: dict[str, str] | None = None,
        from_version: int | None = None,
    ) -> TenantBundle:
        """A new draft. Unset parts are copied from `from_version` (default: the active version), else from the file."""
        async with self._lock(tenant_id):
            base = (
                await self.store.get(tenant_id, from_version)
                if from_version
                else self._active.get(tenant_id) or self._drafts.get(tenant_id)
            )
            if from_version and base is None:
                raise NotFoundError(f"{tenant_id} has no version {from_version}")
            file_config = self._files.get(tenant_id)
            chosen = config or (base.config if base else file_config)
            if chosen is None:
                raise BundleError(f"{tenant_id} has no configuration to start from: pass a config")
            fields: dict[str, Any] = {
                "tenant_id": tenant_id,
                "status": "draft",
                "created_at": self._clock.now(),
                "created_by": created_by,
                "parent_version": base.version if base else None,
                "config": chosen,
                "profile": profile or (base.profile if base else _profile_of(chosen)),
                "knowledge": knowledge or (base.knowledge if base else BundleKnowledge()),
                "guardrails": guardrails or (base.guardrails if base else BundleGuardrails()),
                "tools": tools if tools is not None else (base.tools if base else ()),
                "escalation": base.escalation if base else None,
                "identities": identities if identities is not None else (dict(base.identities) if base else {}),
            }
            for _ in range(5):  # another writer may take the number: try the next one
                fields["version"] = await self.store.latest_version(tenant_id) + 1
                if fields["parent_version"] is not None and fields["parent_version"] >= fields["version"]:
                    fields["parent_version"] = None
                try:
                    bundle = TenantBundle.model_validate(fields)
                except ValueError as exc:
                    raise BundleError(str(exc)) from None
                try:
                    await self.store.add(bundle)
                except AlreadyExistsError:
                    continue
                if tenant_id not in self._active and tenant_id not in self._files:
                    await self._use_draft(bundle)
                return bundle
            raise BundleError("could not take a version number; try again")

    async def publish(self, tenant_id: str, version: int) -> TenantBundle:
        async with self._lock(tenant_id):
            bundle = await self._get(tenant_id, version)
            if bundle.status != "draft":
                raise BundleError(f"version {version} is already {bundle.status}")
            problems = [p for check in self._publish_checks for p in await check(bundle)]
            if problems:
                raise BundleError("cannot publish: " + "; ".join(problems))
            return await self.store.set_status(tenant_id, version, "published")

    async def activate(self, tenant_id: str, version: int, actor: str) -> TenantBundle:
        async with self._lock(tenant_id):
            await self._get(tenant_id, version)
            try:
                bundle = await self.store.activate(tenant_id, version, actor, self._clock.now())
            except StoreError as exc:
                raise BundleError(str(exc)) from None
            self._apply(bundle)
            await self._notify(bundle, tenant_id)
            return bundle

    async def rollback(self, tenant_id: str, actor: str) -> TenantBundle:
        """Activate the parent of the active version."""
        active = self._active.get(tenant_id)
        if active is None:
            raise BundleError(f"{tenant_id} has no active bundle to roll back")
        if active.parent_version is None:
            raise BundleError(f"version {active.version} has no earlier version to go back to")
        return await self.activate(tenant_id, active.parent_version, actor)

    async def deactivate(self, tenant_id: str) -> None:
        """Forget the active bundle in memory so the tenant file applies again (used by the CLI and tests)."""
        async with self._lock(tenant_id):
            self._apply(None, tenant_id)
            await self._notify(None, tenant_id)

    async def _get(self, tenant_id: str, version: int) -> TenantBundle:
        bundle = await self.store.get(tenant_id, version)
        if bundle is None:
            raise NotFoundError(f"{tenant_id} has no version {version}")
        return bundle

    async def get_bundle(self, tenant_id: str, version: int) -> TenantBundle:
        return await self._get(tenant_id, version)

    def require_known(self, tenant_id: str) -> None:
        if tenant_id not in self and tenant_id not in self._files:
            raise UnknownTenantError(tenant_id)


def _profile_of(config: TenantConfig) -> BundleProfile:
    return BundleProfile(
        display_name=config.display_name, locales=(config.default_locale,), default_locale=config.default_locale
    )


def tools_from_config(config: TenantConfig, binding: "TenantBinding | None" = None) -> tuple[BundleTool, ...]:
    """The tools a configuration relies on, as bundle entries. `binding`: what the tools service serves."""
    from agent_service.actions.permissions import configured_capabilities

    tools: list[BundleTool] = []
    for name in configured_capabilities(config):
        bound = binding.tools.get(name) if binding is not None else None
        if bound is None or binding is None:
            tools.append(BundleTool(name=name, source="standin"))
            continue
        tools.append(
            BundleTool(
                name=name,
                source="team_c",
                binding=ToolBindingRef(
                    business_id=binding.business_id,
                    proposal_id=bound.proposal_id or bound.tool or name,
                    environments=tuple(bound.environments),
                    arguments=dict(bound.arguments),
                    fields=dict(bound.fields),
                    reference_field=bound.reference_field,
                    echo=tuple(bound.echo),
                    constants=dict(bound.constants),
                    spec=bound.spec,
                ),
            )
        )
    return tuple(tools)


async def import_from_files(
    registry: BundleRegistry,
    tenant_id: str,
    created_by: str,
    binding: "TenantBinding | None" = None,
    knowledge: BundleKnowledge | None = None,
    guardrails: BundleGuardrails | None = None,
) -> TenantBundle:
    """A bundle for a business that only has a file: the same configuration, as a draft ready to publish."""
    config = registry.file_config(tenant_id)
    if config is None:
        raise BundleError(f"{tenant_id} has no tenant file to import")
    return await registry.create_draft(
        tenant_id,
        created_by=created_by,
        config=config,
        tools=tools_from_config(config, binding),
        knowledge=knowledge,
        guardrails=guardrails,
        from_version=None,
    )
