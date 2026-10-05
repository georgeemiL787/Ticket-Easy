"""StandinShop: the fake Nile Style shop behind the CapabilityClient plug.

It serves the tools of fixtures/<tenant>/tools.json over the data in backend.json (a fresh deep copy per instance, so
tests never share state), keeps an audit log of every call, replays idempotent writes, refuses writes that carry no
policy_request_id, and has switches to simulate every kind of failure.
"""

import copy
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from team_b.adapters.standins import shop_backend as backend
from team_b.adapters.standins.json_schema import validate
from team_b.contracts.errors import UpstreamError
from team_b.contracts.tools import ToolCallRequest, ToolResult, ToolSpec
from team_b.ports import Clock

SERVICE = "shop"
FAIL_CODES = ("BACKEND_UNAVAILABLE", "TIMEOUT", "NOT_FOUND")
RETRYABLE = ("BACKEND_UNAVAILABLE", "TIMEOUT")
_RAW: dict[Path, Any] = {}  # parsed fixture files, deep-copied on every load


class AuditEntry(BaseModel):
    """One call to the shop, as the shop saw it."""

    model_config = ConfigDict(frozen=True)

    seq: int
    tenant_id: str
    tool: str
    request_id: str
    arguments: dict[str, Any]
    actor: str
    idempotency_key: str
    policy_request_id: str | None
    approval_id: str | None
    status: str
    error_code: str | None
    audit_id: str | None  # what the caller was given; None for errors and for the no_audit switch
    reference_id: str | None
    applied: bool  # did this call change the shop? (true even for an unclear result that really happened)
    replayed: bool  # answered from an earlier identical call, nothing changed
    at: datetime


@dataclass
class _Tenant:
    tenant_id: str
    tools: dict[str, ToolSpec]
    shop: backend.Backend
    audit: list[AuditEntry] = field(default_factory=list)
    replay: dict[str, tuple[str, str, ToolResult]] = field(default_factory=dict)


def _read(path: Path) -> Any:
    if path not in _RAW:
        _RAW[path] = json.loads(path.read_text(encoding="utf-8"))
    return copy.deepcopy(_RAW[path])


def _error(code: str, message: str, *, unclear: bool = False) -> ToolResult:
    return ToolResult(
        status="error",
        error_code=code,
        error_message=message,
        retryable=code in RETRYABLE,
        write_may_have_applied=unclear,
    )


class StandinShop:
    def __init__(self, fixtures_dir: Path, clock: Clock, tenant_ids: Iterable[str] = ()) -> None:
        self._dir = fixtures_dir
        self._clock = clock
        self._tenants: dict[str, _Tenant] = {}
        self._fail: dict[str, list[list[Any]]] = {}  # tool -> [[code, times left], ...]
        self._uncertain: dict[str, bool] = {}  # tool -> did the write really happen?
        self._no_audit: set[str] = set()
        self._unpublished: set[str] = set()
        for tenant_id in tenant_ids:
            self._tenant(tenant_id)

    # ---- the CapabilityClient plug ----

    async def list_tools(self, tenant_id: str) -> list[ToolSpec]:
        tenant = self._tenant(tenant_id)
        return [s.model_copy(deep=True) for s in tenant.tools.values() if self._published(s)]

    async def call_tool(self, tenant_id: str, request: ToolCallRequest) -> ToolResult:
        t = self._tenant(tenant_id)
        spec = t.tools.get(request.tool)
        if spec is None or not self._published(spec):
            return self._log(t, request, _error("TOOL_NOT_PUBLISHED", f"tool {request.tool} is not published"))
        key = json.dumps(request.arguments, sort_keys=True, default=str)
        earlier = t.replay.get(request.idempotency_key)
        if earlier is not None:  # same key: answer with the original result, change nothing
            if (earlier[0], earlier[1]) != (request.tool, key):
                return self._log(
                    t, request, _error("IDEMPOTENCY_CONFLICT", "this idempotency key was used for another call")
                )
            return self._log(t, request, earlier[2].model_copy(deep=True), replayed=True)
        write = spec.operation_kind != "read"
        if spec.human_only and request.actor != "human":
            return self._log(t, request, _error("HUMAN_ONLY", f"{request.tool} may only be used by a human"))
        if write and not request.policy_request_id:
            return self._log(
                t, request, _error("POLICY_REQUIRED", "a change needs the policy_request_id that allowed it")
            )
        problems = validate(spec.input_schema, request.arguments)
        if problems:
            return self._log(t, request, _error("INVALID_ARGUMENTS", "; ".join(problems)))
        injected = self._take_failure(request.tool)
        if injected is not None:
            result = _error(injected, f"injected failure: {injected}")
            self._log(t, request, result)
            if injected in RETRYABLE:
                raise UpstreamError(SERVICE, injected, f"injected failure on {request.tool}", retryable=True)
            return result
        applied = False
        if write and request.tool in self._uncertain:
            if self._uncertain[request.tool]:
                outcome = backend.HANDLERS[request.tool](t.shop, request.arguments)
                if isinstance(outcome, backend.Fail):
                    return self._log(t, request, _error(outcome.code, outcome.message))
                applied = True
            unclear = _error("OUTCOME_UNKNOWN", "the shop did not confirm whether the change happened", unclear=True)
            return self._log(t, request, unclear, applied=applied)
        return self._finish(t, request, write, key)

    # ---- failure switches (for tests and scenarios) ----

    def fail_next(self, tool: str, code: str, times: int = 1) -> None:
        """The next `times` calls of `tool` fail with `code`. BACKEND_UNAVAILABLE and TIMEOUT raise UpstreamError
        (the shop could not be reached, nothing changed); NOT_FOUND comes back as an error result."""
        if code not in FAIL_CODES:
            raise ValueError(f"code must be one of {FAIL_CODES}")
        if times < 1:
            raise ValueError("times must be at least 1")
        self._check_tool(tool)
        self._fail.setdefault(tool, []).append([code, times])

    def uncertain(self, tool: str, *, applied: bool = False) -> None:
        """Calls of the write tool answer OUTCOME_UNKNOWN with write_may_have_applied. `applied` says whether the
        change really happened in the shop, so tests can check that the brain never claims done or failed."""
        if self._check_tool(tool).operation_kind == "read":
            raise ValueError("uncertain() only makes sense for a write tool")
        self._uncertain[tool] = applied

    def no_audit(self, tool: str) -> None:
        """Calls of `tool` succeed but come back without an audit_id (an unverifiable success)."""
        self._check_tool(tool)
        self._no_audit.add(tool)

    def unpublish(self, tool: str) -> None:
        """The tool disappears from list_tools and calls answer TOOL_NOT_PUBLISHED."""
        self._check_tool(tool)
        self._unpublished.add(tool)

    def publish(self, tool: str) -> None:
        self._unpublished.discard(tool)

    def reset(self) -> None:
        """Clear every switch and reload the data, audit log and idempotency memory of every tenant."""
        self._fail.clear()
        self._uncertain.clear()
        self._no_audit.clear()
        self._unpublished.clear()
        loaded = list(self._tenants)
        self._tenants.clear()
        for tenant_id in loaded:
            self._tenant(tenant_id)

    def inject(self, spec: Mapping[str, Any]) -> None:
        """Apply a switch from a scenario file, e.g. {"switch": "fail_next", "tool": "get_order", "code": "TIMEOUT"}."""
        switch = spec.get("switch")
        tool = spec.get("tool")
        if switch == "fail_next":
            self.fail_next(str(tool), str(spec["code"]), int(spec.get("times", 1)))
        elif switch == "uncertain":
            self.uncertain(str(tool), applied=bool(spec.get("applied", False)))
        elif switch in ("no_audit", "unpublish", "publish"):
            getattr(self, switch)(str(tool))
        elif switch == "reset":
            self.reset()
        else:
            raise ValueError(f"unknown shop switch: {switch!r}")

    # ---- what happened (for assertions) ----

    def audit_log(self, tenant_id: str) -> list[AuditEntry]:
        return list(self._tenant(tenant_id).audit)

    def change_count(self, tenant_id: str) -> int:
        """How many calls really changed the shop. Safety tests count this, not what the agent said."""
        return sum(1 for e in self._tenant(tenant_id).audit if e.applied)

    def records(self, tenant_id: str, kind: str) -> list[dict[str, Any]]:
        """Created returns, exchanges, refunds, vouchers, tickets or deletions."""
        return copy.deepcopy(self._tenant(tenant_id).shop.records[kind])

    def order(self, tenant_id: str, order_id: str) -> dict[str, Any] | None:
        found = self._tenant(tenant_id).shop.order(order_id)
        return copy.deepcopy(found) if found else None

    # ---- internals ----

    def _published(self, spec: ToolSpec) -> bool:
        return spec.enabled and spec.name not in self._unpublished

    def _check_tool(self, tool: str) -> ToolSpec:
        for attempt in range(2):
            for tenant in self._tenants.values():
                if tool in tenant.tools:
                    return tenant.tools[tool]
            if attempt == 0:  # a switch may be flipped before any call: load every tenant that has shop data
                for tools_file in sorted(self._dir.glob("*/tools.json")):
                    self._tenant(tools_file.parent.name)
        raise ValueError(f"unknown tool: {tool}")

    def _take_failure(self, tool: str) -> str | None:
        queue = self._fail.get(tool)
        if not queue:
            return None
        code: str = queue[0][0]
        queue[0][1] -= 1
        if queue[0][1] <= 0:
            queue.pop(0)
        return code

    def _tenant(self, tenant_id: str) -> _Tenant:
        if tenant_id not in self._tenants:
            folder = self._dir / tenant_id
            if not (folder / "tools.json").is_file() or not (folder / "backend.json").is_file():
                raise UpstreamError(SERVICE, "TENANT_NOT_FOUND", f"no shop data for tenant {tenant_id}")
            tools = {t["name"]: ToolSpec.model_validate(t) for t in _read(folder / "tools.json")["tools"]}
            data = _read(folder / "backend.json")
            shop = backend.Backend(
                customers={c["customer_id"]: c for c in data["customers"]},
                orders={o["order_id"]: o for o in data["orders"]},
                today=self._clock.today,
            )
            self._tenants[tenant_id] = _Tenant(tenant_id=tenant_id, tools=tools, shop=shop)
        return self._tenants[tenant_id]

    def _finish(self, t: _Tenant, request: ToolCallRequest, write: bool, key: str) -> ToolResult:
        outcome = backend.HANDLERS[request.tool](t.shop, request.arguments)
        if isinstance(outcome, backend.Fail):
            return self._log(t, request, _error(outcome.code, outcome.message))
        audit_id = None if request.tool in self._no_audit else f"AUD-{len(t.audit) + 1:05d}"
        result = ToolResult(status="success", data=outcome.data, audit_id=audit_id, reference_id=outcome.reference_id)
        if write:
            t.replay[request.idempotency_key] = (request.tool, key, result.model_copy(deep=True))
        return self._log(t, request, result, applied=outcome.applied)

    def _log(
        self, t: _Tenant, request: ToolCallRequest, result: ToolResult, *, applied: bool = False, replayed: bool = False
    ) -> ToolResult:
        t.audit.append(
            AuditEntry(
                seq=len(t.audit) + 1,
                tenant_id=t.tenant_id,
                tool=request.tool,
                request_id=request.request_id,
                arguments=copy.deepcopy(request.arguments),
                actor=request.actor,
                idempotency_key=request.idempotency_key,
                policy_request_id=request.policy_request_id,
                approval_id=request.approval_id,
                status=result.status,
                error_code=result.error_code,
                audit_id=result.audit_id,
                reference_id=result.reference_id,
                applied=applied,
                replayed=replayed,
                at=self._clock.now(),
            )
        )
        return result
