"""Team C's sandbox-published business tools behind Team B's CapabilityClient plug (TEST-ONLY).

TeamCCapabilities wraps the existing shop client (the stand-in) and sends the tools a business BINDS to Team C over MCP
instead. Everything else in the brain is unchanged: the same ToolSpec names the intents use (get_order, create_ticket),
the same gates, the same verify_result.

Binding (an operator file, TEAM_B_TEAM_C_CONFIG, see config/team_c.example.json):
- A Team B tool name is bound to a Team C proposal (`proposal_id`, stable across versions and rebuilds) or to one exact
  published tool name. On every listing the published tool of that proposal with the highest version is used; two
  published artifacts of the same version are ambiguous and nothing is used (disable one in Team C).
- A bound tool is NEVER served by the stand-in: when Team C does not list it (not published, disabled, blocked, Team C
  down), the brain sees it as not published and says so or hands over.
- The ToolSpec the brain sees is the business's own declaration (the stand-in's spec of that name, or `spec` in the
  binding). A Team C tool that writes while the declaration says read (or the reverse) is not used (fail closed).
- `arguments` / `fields` rename Team B's argument and output names to Team C's; outputs not named are dropped.

Identity: Team C's MCP server is bound to one sandbox identity per process. The identity of a call is chosen from the
customer the brain VERIFIED (ToolCallRequest.acting_customer_id, set by code) through the operator's `identities` map,
one process per (business, identity). Arguments never choose it. With no verified or mapped customer the call is not
sent (IDENTITY_NOT_MAPPED), except a tool that needs no identity may use `anonymous_identity` when one is configured.

Results (only `succeeded` is a success; nothing is retried here):
- succeeded -> success, audit_id "team-c:<execution_id>", reference_id from `reference_field` for creates
- rejected (refused before any request) -> error TEAM_C_REJECTED, nothing applied
- failed -> error TEAM_C_FAILED; when the API refused a write (write_state rejected_unverified) Team C does not assert
  that nothing changed, so write_may_have_applied (the brain escalates as unverified_result, never says "not done")
- partial / outcome_unknown -> error PARTIAL_WRITE / OUTCOME_UNKNOWN, write_may_have_applied
- error -> error TEAM_C_ERROR, write_may_have_applied for a write tool
- JSON-RPC tool_not_published -> error TOOL_NOT_PUBLISHED (the registry forgets its list); the request was not run
- request sent but no answer (timeout, process exit) -> UpstreamError TIMEOUT / CONNECTION_LOST: the brain reports a
  write as uncertain (never BACKEND_UNAVAILABLE, which means "certainly not applied"); a read may be asked again
- process not running / cannot start -> UpstreamError BACKEND_UNAVAILABLE (nothing was sent)
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from team_b.adapters.team_c_admin import TeamCAdmin
from team_b.adapters.team_c_mcp import McpLost, McpRemoteError, McpStdioSession, McpUnavailable
from team_b.contracts.errors import UpstreamError
from team_b.contracts.tools import ToolCallRequest, ToolResult, ToolSpec
from team_b.observability import get_logger
from team_b.ports import CapabilityClient

SERVICE = "team_c"
AUDIT_PREFIX = "team-c:"
NOT_SENT_STATES = (None, "not_attempted", "not_applied")
START_RETRY_S = 5.0  # after a failed start the process is not tried again for this long (no hammering)
log = get_logger(__name__)


class ToolBinding(BaseModel):
    """One Team B tool served by a Team C publication."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: str | None = None  # the Team C tool across versions: its newest published version is used
    tool: str | None = None  # or one exact published tool name (<slug>_v<n>_<artifact hex>)
    arguments: dict[str, str] = Field(default_factory=dict)  # Team B argument name -> Team C input name
    fields: dict[str, str] = Field(default_factory=dict)  # Team B output field -> Team C output name or /json/pointer
    reference_field: str | None = None  # for a create: the (Team B) output field that is the new record's reference
    spec: dict[str, Any] | None = None  # the ToolSpec when the stand-in has none of this name (capability, kind, ...)

    @model_validator(mode="after")
    def _names_one_tool(self) -> "ToolBinding":
        if bool(self.proposal_id) == bool(self.tool):
            raise ValueError("a binding names exactly one of proposal_id or tool")
        return self


class TenantBinding(BaseModel):
    """A business served by Team C: its Team C business, who acts for whom, and which tools Team C serves."""

    model_config = ConfigDict(extra="forbid")

    business_id: str = Field(min_length=1)
    identities: dict[str, str] = Field(default_factory=dict)  # verified customer_id -> Team C sandbox identity name
    anonymous_identity: str | None = None  # only for tools that need no identity (requires_identity false)
    list_identity: str | None = None  # the process that lists tools (default: the first mapped identity)
    tools: dict[str, ToolBinding] = Field(default_factory=dict)

    def lister(self) -> str | None:
        return self.list_identity or next(iter(sorted(self.identities.values())), None) or self.anonymous_identity


class TeamCConfig(BaseModel):
    """The operator's Team C connection (TEAM_B_TEAM_C_CONFIG). Paths may be relative to the file's folder."""

    model_config = ConfigDict(extra="forbid")

    command: list[str] = Field(min_length=1)  # e.g. [".../team_c/.venv/Scripts/python.exe", "-m", "team_c.mcp_server"]
    cwd: str | None = None  # Team C's folder (the server imports team_c from there)
    env: dict[str, str] = Field(default_factory=dict)  # e.g. {"PYTHONPATH": "."}
    database: str | None = None  # --database (Team C's SQLite file)
    connectors: str | None = None  # --connectors (optional trusted connectors file)
    sandbox_hosts: str | None = None  # --sandbox-hosts
    api_url: str | None = None  # Team C's REST API (http://127.0.0.1:<port>) for the Business tools page
    builder_url: str | None = None  # Team C's owner pages, linked from the Business tools page
    list_timeout_s: float = Field(default=15.0, gt=0)
    call_timeout_s: float = Field(default=30.0, gt=0)
    start_timeout_s: float = Field(default=30.0, gt=0)
    tenants: dict[str, TenantBinding] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "TeamCConfig":
        config = cls.model_validate(json.loads(path.read_text(encoding="utf-8")))
        base = path.resolve().parent
        resolve = lambda p: str((base / p).resolve()) if p and not Path(p).is_absolute() else p  # noqa: E731
        program = config.command[0]
        if not Path(program).is_absolute() and ("/" in program or "\\" in program):  # a path, not a name on PATH
            program = str((base / program).resolve())
        return config.model_copy(update={"cwd": resolve(config.cwd), "command": [program, *config.command[1:]]})

    def server_command(self, business_id: str, identity: str) -> list[str]:
        command = [*self.command, "--business-id", business_id, "--identity", identity]
        for flag, value in (("--database", self.database), ("--connectors", self.connectors),
                            ("--sandbox-hosts", self.sandbox_hosts)):  # fmt: skip
            if value:
                command += [flag, value]
        return command


@dataclass
class ResolvedTool:
    """What a Team B tool name points at right now in Team C (or why it points at nothing)."""

    name: str  # Team B's name
    team_c_tool: str | None = None
    publication_id: str | None = None
    artifact_id: str | None = None
    artifact_sha256: str | None = None
    proposal_id: str | None = None
    proposal_version: int | None = None
    read_only: bool | None = None
    problem: str | None = None  # not_published | ambiguous | kind_mismatch | no_spec | team_c_unavailable


@dataclass
class TenantState:
    listed_at: float | None = None  # time.time() of the last successful listing
    error: str | None = None  # why the last listing failed
    resolved: dict[str, ResolvedTool] = field(default_factory=dict)
    tools: list[dict[str, Any]] = field(default_factory=list)  # every Team C tool the last listing returned
    declared: dict[str, ToolSpec] = field(default_factory=dict)  # the business's own ToolSpecs (the stand-in's)


class TeamCCapabilities:
    """CapabilityClient: tools bound to Team C go over MCP; every other tool goes to `fallback` unchanged."""

    def __init__(self, fallback: CapabilityClient, config: TeamCConfig) -> None:
        self._fallback = fallback
        self.config = config
        self._sessions: dict[tuple[str, str], McpStdioSession] = {}
        self._starting: dict[tuple[str, str], asyncio.Lock] = {}
        self._start_failed: dict[tuple[str, str], tuple[float, str]] = {}
        self._state: dict[str, TenantState] = {}
        # Team C's REST API, for the Business tools page (publications, proposals, disable); None without api_url.
        self.admin: TeamCAdmin | None = TeamCAdmin(config.api_url) if config.api_url else None

    # ---- the CapabilityClient plug ----

    async def list_tools(self, tenant_id: str) -> list[ToolSpec]:
        own = await self._fallback.list_tools(tenant_id)
        binding = self.config.tenants.get(tenant_id)
        if binding is None:
            return own
        state = await self.refresh(tenant_id, {t.name: t for t in own})
        served: list[ToolSpec] = [t for t in own if t.name not in binding.tools]
        for name in binding.tools:
            resolved = state.resolved.get(name)
            spec = self._spec(tenant_id, name)
            if resolved is not None and resolved.problem is None and spec is not None:
                served.append(spec)
        return served

    async def call_tool(self, tenant_id: str, request: ToolCallRequest) -> ToolResult:
        binding = self.config.tenants.get(tenant_id)
        bound = binding.tools.get(request.tool) if binding is not None else None
        if binding is None or bound is None:
            return await self._fallback.call_tool(tenant_id, request)
        state = self._state.get(tenant_id)
        if state is None or request.tool not in state.resolved:
            await self.refresh(tenant_id)
            state = self._state[tenant_id]
        resolved = state.resolved.get(request.tool)
        if resolved is None or resolved.problem is not None or resolved.team_c_tool is None:
            why = resolved.problem if resolved is not None else "not_published"
            return _error("TOOL_NOT_PUBLISHED", f"{request.tool} has no usable Team C sandbox publication ({why})")
        spec = self._spec(tenant_id, request.tool)
        identity = self._identity(binding, request, spec)
        if identity is None:
            return _error("IDENTITY_NOT_MAPPED", "no Team C sandbox identity is configured for this customer; not sent")
        arguments = {bound.arguments.get(k, k): v for k, v in request.arguments.items()}
        writes = resolved.read_only is False
        try:
            session = await self._session(binding.business_id, identity)
            raw = await session.call_tool(resolved.team_c_tool, arguments, timeout_s=self.config.call_timeout_s)
        except McpUnavailable as exc:
            raise UpstreamError(SERVICE, "BACKEND_UNAVAILABLE", str(exc), retryable=True) from None
        except McpLost as exc:
            code = "TIMEOUT" if "within" in str(exc) else "CONNECTION_LOST"
            raise UpstreamError(SERVICE, code, f"{exc}; the outcome is unknown", retryable=not writes) from None
        except McpRemoteError as exc:
            return self._remote_error(tenant_id, request.tool, exc, writes)
        return _result(raw, bound, resolved, writes)

    # ---- discovery ----

    async def refresh(self, tenant_id: str, declared: dict[str, ToolSpec] | None = None) -> TenantState:
        """List Team C's tools again (no list_changed notifications exist) and resolve every binding."""
        binding = self.config.tenants[tenant_id]
        if declared is None:
            declared = {t.name: t for t in await self._fallback.list_tools(tenant_id)}
        state = self._state.setdefault(tenant_id, TenantState())
        state.declared = declared
        lister = binding.lister()
        try:
            if lister is None:
                raise McpUnavailable("no sandbox identity is configured for this business")
            session = await self._session(binding.business_id, lister)
            tools = await session.list_tools(timeout_s=self.config.list_timeout_s)
        except (McpUnavailable, McpLost, McpRemoteError) as exc:
            state.error = str(exc)[:300]
            state.resolved = {n: ResolvedTool(n, problem="team_c_unavailable") for n in binding.tools}
            log.warning("team_c_list_failed", tenant_id=tenant_id, error=state.error)
            return state
        state.listed_at, state.error, state.tools = time.time(), None, tools
        state.resolved = {
            name: _resolve(name, bound, tools, self._spec(tenant_id, name)) for name, bound in binding.tools.items()
        }
        return state

    def status(self, tenant_id: str) -> TenantState | None:
        return self._state.get(tenant_id)

    def binding(self, tenant_id: str) -> TenantBinding | None:
        return self.config.tenants.get(tenant_id)

    def server_stderr(self, tenant_id: str) -> list[str]:
        binding = self.config.tenants.get(tenant_id)
        lister = binding.lister() if binding is not None else None
        session = self._sessions.get((binding.business_id, lister)) if binding is not None and lister else None
        return session.recent_stderr if session is not None else []

    async def aclose(self) -> None:
        for session in self._sessions.values():
            session.close()
        self._sessions.clear()

    # ---- internals ----

    def _spec(self, tenant_id: str, name: str) -> ToolSpec | None:
        """The business's own declaration of this tool: the binding's `spec`, else the stand-in's spec of that name."""
        bound = self.config.tenants[tenant_id].tools[name]
        if bound.spec is not None:
            return ToolSpec.model_validate({"name": name, "capability": name, **bound.spec})
        state = self._state.get(tenant_id)
        return state.declared.get(name) if state is not None else None

    @staticmethod
    def _identity(binding: TenantBinding, request: ToolCallRequest, spec: ToolSpec | None) -> str | None:
        if request.acting_customer_id and request.acting_customer_id in binding.identities:
            return binding.identities[request.acting_customer_id]
        if spec is not None and not spec.requires_identity and binding.anonymous_identity:
            return binding.anonymous_identity
        return None

    async def _session(self, business_id: str, identity: str) -> McpStdioSession:
        key = (business_id, identity)
        session = self._sessions.get(key)
        if session is not None and session.alive:
            return session
        lock = self._starting.setdefault(key, asyncio.Lock())
        async with lock:
            session = self._sessions.get(key)
            if session is not None and session.alive:
                return session
            failed = self._start_failed.get(key)
            if failed is not None and time.monotonic() - failed[0] < START_RETRY_S:
                raise McpUnavailable(failed[1])
            session = McpStdioSession(
                self.config.server_command(business_id, identity),
                cwd=self.config.cwd,
                env=self.config.env,
                start_timeout_s=self.config.start_timeout_s,
            )
            try:
                await session.start()
            except McpUnavailable as exc:
                self._start_failed[key] = (time.monotonic(), str(exc))
                raise
            self._start_failed.pop(key, None)
            self._sessions[key] = session
            log.info("team_c_mcp_started", business_id=business_id, protocol=session.protocol_version)
            return session

    def _remote_error(self, tenant_id: str, tool: str, exc: McpRemoteError, writes: bool) -> ToolResult:
        data = exc.data if isinstance(exc.data, dict) else {}
        if data.get("code") == "tool_not_published":
            state = self._state.get(tenant_id)
            if state is not None:
                state.resolved.pop(tool, None)  # the next call or listing asks Team C again
            return _error("TOOL_NOT_PUBLISHED", f"{tool}: Team C says the tool is not published (disabled or replaced)")
        # -32602 / -32601 are refusals before the tool ran; any other protocol error may have happened mid-call.
        before_run = exc.code in (-32602, -32601, -32600)
        return _error("TEAM_C_PROTOCOL_ERROR", f"Team C answered {exc.code}", unclear=writes and not before_run)


def _resolve(name: str, bound: ToolBinding, tools: list[dict[str, Any]], spec: ToolSpec | None) -> ResolvedTool:
    if spec is None:
        return ResolvedTool(name, problem="no_spec")
    candidates = [
        t
        for t in tools
        if (t.get("name") == bound.tool if bound.tool else _meta(t).get("proposal_id") == bound.proposal_id)
    ]
    if not candidates:
        return ResolvedTool(name, problem="not_published")
    newest = max(int(_meta(t).get("proposal_version") or 0) for t in candidates)
    top = [t for t in candidates if int(_meta(t).get("proposal_version") or 0) == newest]
    if len(top) > 1:
        return ResolvedTool(name, problem="ambiguous")
    tool, meta = top[0], _meta(top[0])
    annotations = tool.get("annotations") or {}
    read_only = annotations.get("readOnlyHint", annotations.get("read_only_hint"))
    resolved = ResolvedTool(
        name,
        team_c_tool=str(tool["name"]),
        publication_id=meta.get("publication_id"),
        artifact_id=meta.get("artifact_id"),
        artifact_sha256=meta.get("artifact_sha256"),
        proposal_id=meta.get("proposal_id"),
        proposal_version=meta.get("proposal_version"),
        read_only=read_only if isinstance(read_only, bool) else None,
    )
    if meta.get("production_ready") is not False or meta.get("environment") != "sandbox":
        resolved.problem = "not_sandbox"  # this integration only ever serves TEST-ONLY sandbox publications
    elif resolved.read_only is None or resolved.read_only != (spec.operation_kind == "read"):
        resolved.problem = "kind_mismatch"  # a tool that writes is never served as a read (or the reverse)
    return resolved


def _meta(tool: dict[str, Any]) -> dict[str, Any]:
    meta = tool.get("_meta") or tool.get("meta") or {}
    team_c = meta.get("team_c") if isinstance(meta, dict) else None
    return team_c if isinstance(team_c, dict) else {}


def _error(code: str, message: str, *, unclear: bool = False) -> ToolResult:
    return ToolResult(status="error", error_code=code, error_message=message[:300], write_may_have_applied=unclear)


_MISSING = object()


def _pick(output: dict[str, Any], where: str) -> Any:
    """Team C output `where` (a name, or a /json/pointer into the outputs); _MISSING when it is not there."""
    value: Any = output
    for part in where.strip("/").split("/") if where.startswith("/") else [where]:
        if not isinstance(value, dict) or part not in value:
            return _MISSING
        value = value[part]
    return value


def _envelope(raw: dict[str, Any]) -> dict[str, Any] | None:
    found = raw.get("structuredContent")
    if isinstance(found, dict):
        return found
    for item in raw.get("content") or []:  # the same JSON mirrored as text (older clients read this)
        if isinstance(item, dict) and item.get("type") == "text":
            try:
                parsed = json.loads(item.get("text") or "")
            except ValueError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return None


def _result(raw: dict[str, Any], bound: ToolBinding, resolved: ResolvedTool, writes: bool) -> ToolResult:
    envelope = _envelope(raw)
    if envelope is None or envelope.get("production_ready") is not False:
        return _error("TEAM_C_BAD_RESULT", "Team C's answer is not a TEST-ONLY execution result", unclear=writes)
    status = envelope.get("status")
    execution = envelope.get("execution_id")
    audit = f"{AUDIT_PREFIX}{execution}" if execution else None
    found_error = envelope.get("error")
    error: dict[str, Any] = found_error if isinstance(found_error, dict) else {}
    detail = f"{error.get('code') or status}: {str(envelope.get('message') or '')[:200]}"
    if status == "succeeded" and raw.get("isError") is not True:
        if envelope.get("publication_id") not in (None, resolved.publication_id):
            return _error("TEAM_C_BAD_RESULT", "Team C answered for another publication", unclear=writes)
        found_output = envelope.get("output")
        output: dict[str, Any] = found_output if isinstance(found_output, dict) else {}
        picked = {mine: _pick(output, theirs) for mine, theirs in bound.fields.items()}
        data = {k: v for k, v in picked.items() if v is not _MISSING} if bound.fields else dict(output)
        reference = data.get(bound.reference_field) if bound.reference_field else None
        return ToolResult(
            status="success", data=data, audit_id=audit, reference_id=str(reference) if reference else None
        )
    writes_seen = [w for w in envelope.get("writes") or [] if isinstance(w, dict)]
    touched = any(w.get("write_state") not in NOT_SENT_STATES for w in writes_seen)
    if status == "rejected":
        return ToolResult(status="error", error_code="TEAM_C_REJECTED", error_message=detail, audit_id=audit)
    if status == "failed":
        return ToolResult(
            status="error",
            error_code="TEAM_C_FAILED",
            error_message=detail,
            audit_id=audit,
            write_may_have_applied=touched,
        )
    if status in ("partial", "outcome_unknown"):
        code = "PARTIAL_WRITE" if status == "partial" else "OUTCOME_UNKNOWN"
        return ToolResult(
            status="error", error_code=code, error_message=detail, audit_id=audit, write_may_have_applied=True
        )
    # "error" (the server failed while handling the call) and anything unknown: a write may have happened.
    code = "TEAM_C_ERROR" if status == "error" else "TEAM_C_BAD_RESULT"
    return ToolResult(
        status="error", error_code=code, error_message=detail, audit_id=audit, write_may_have_applied=writes or touched
    )
