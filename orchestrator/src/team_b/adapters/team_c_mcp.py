"""A small MCP client over stdio: start one server process, initialize, list tools, call a tool.

It speaks the MCP JSON-RPC protocol (newline-delimited JSON on the process's stdin/stdout) to Team C's sandbox server
(`python -m team_c.mcp_server`). Only what Team B needs is here: the initialize handshake, tools/list with paging and
tools/call. The server's own requests (ping) are answered; anything else it asks is refused politely.

The process runs under subprocess.Popen with reader threads, so it works under every asyncio event loop (uvicorn on
Windows may run a selector loop, which cannot start asyncio subprocesses).

Failures say whether the request may have reached the server, because a write must never be reported as "not done"
when it may have run:
- McpUnavailable: nothing was sent (the process could not start, or died before the request was written).
- McpLost: the request was written, then the answer never came (timeout, process exit). The outcome is unknown.
- McpRemoteError: the server answered with a JSON-RPC error (code, message, data).
"""

import itertools
import json
import os
import subprocess
import threading
from asyncio import AbstractEventLoop, Future, get_running_loop, wait_for
from collections import deque
from collections.abc import Mapping, Sequence
from typing import Any

from team_b.observability import get_logger

log = get_logger(__name__)

PROTOCOL_VERSION = "2025-06-18"  # an initialize-handshake revision Team C's SDK (mcp 2.2.0) negotiates
CLIENT_INFO = {"name": "ticket-easy-team-b", "version": "0.1.0"}
STDERR_LINES = 40  # the server's last log lines, kept for diagnostics (Team C logs no request URLs or credentials)
# Environment variables passed through to the server process; Team B's own settings (keys) are never passed on.
PASS_THROUGH_ENV = (
    "PATH",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
)


class McpError(Exception):
    """Base class of the client's failures."""


class McpUnavailable(McpError):
    """The request was NOT sent: the server process is not running or could not start."""


class McpLost(McpError):
    """The request was sent but no answer came back (timeout or the process ended). Its outcome is unknown."""


class McpRemoteError(McpError):
    """The server answered with a JSON-RPC error."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.data = data


class McpStdioSession:
    """One running MCP server process and its initialized session. Thread-safe writes; answers matched by id."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        start_timeout_s: float = 30.0,
    ) -> None:
        self._command = list(command)
        self._cwd = cwd
        self._env = dict(env or {})
        self._start_timeout_s = start_timeout_s
        self._process: subprocess.Popen[bytes] | None = None
        self._loop: AbstractEventLoop | None = None
        self._pending: dict[int, Future[dict[str, Any]]] = {}
        self._ids = itertools.count(1)
        self._write_lock = threading.Lock()
        self._stderr: deque[str] = deque(maxlen=STDERR_LINES)
        self._closed = False
        self.server_info: dict[str, Any] = {}
        self.protocol_version: str | None = None

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.poll() is None and not self._closed

    @property
    def recent_stderr(self) -> list[str]:
        return list(self._stderr)

    async def start(self) -> None:
        """Start the process and run the initialize handshake. Raises McpUnavailable when that fails."""
        self._loop = get_running_loop()
        self._process = self._spawn()
        threading.Thread(target=self._read_stdout, name="mcp-stdout", daemon=True).start()
        threading.Thread(target=self._read_stderr, name="mcp-stderr", daemon=True).start()
        try:
            result = await self.request(
                "initialize",
                {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO},
                timeout_s=self._start_timeout_s,
            )
            self._notify("notifications/initialized")
        except McpError as exc:
            detail = self._stderr[-1] if self._stderr else str(exc)
            self.close()
            raise McpUnavailable(f"the MCP server did not initialize: {detail[:300]}") from None
        self.protocol_version = str(result.get("protocolVersion") or "")
        self.server_info = dict(result.get("serverInfo") or {})

    async def list_tools(self, *, timeout_s: float) -> list[dict[str, Any]]:
        """Every tool the server lists now (all pages)."""
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(100):  # a runaway guard; Team C lists one page
            params: dict[str, Any] = {"cursor": cursor} if cursor else {}
            result = await self.request("tools/list", params, timeout_s=timeout_s)
            tools.extend(t for t in result.get("tools") or [] if isinstance(t, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    async def call_tool(
        self, name: str, arguments: dict[str, Any], *, timeout_s: float, meta: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """The raw CallToolResult. Raises McpUnavailable (not sent), McpLost (outcome unknown) or McpRemoteError.
        `meta` is the request's `_meta` (a production call carries ticketeasy.context and
        ticketeasy.idempotency_key there, never in the arguments)."""
        params: dict[str, Any] = {"name": name, "arguments": arguments}
        if meta:
            params["_meta"] = meta
        return await self.request("tools/call", params, timeout_s=timeout_s)

    async def request(self, method: str, params: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        if self._loop is None or not self.alive:
            raise McpUnavailable("the MCP server is not running")
        request_id = next(self._ids)
        future: Future[dict[str, Any]] = self._loop.create_future()
        self._pending[request_id] = future
        try:
            self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        except McpUnavailable:
            self._pending.pop(request_id, None)
            raise
        try:
            message = await wait_for(future, timeout=timeout_s)
        except TimeoutError:
            self._pending.pop(request_id, None)
            # The server may still be working on it; a later answer would arrive for an id nobody waits for. Start
            # clean next time: the outcome of this request stays unknown and is never repeated by this client.
            self.close()
            raise McpLost(f"no answer to {method} within {timeout_s:g} s") from None
        error = message.get("error")
        if isinstance(error, dict):
            raise McpRemoteError(int(error.get("code", 0)), str(error.get("message", "")), error.get("data"))
        result = message.get("result")
        return result if isinstance(result, dict) else {}

    def close(self) -> None:
        """Stop the process. Requests still waiting end as McpLost (their outcome is unknown)."""
        self._closed = True
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            try:
                process.stdin.close() if process.stdin else None
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
        self._fail_pending(McpLost("the MCP server stopped before answering"))

    # ---- internals ----

    def _spawn(self) -> "subprocess.Popen[bytes]":
        """Start the process (returns at once; the handshake is awaited by start())."""
        env = {k: os.environ[k] for k in PASS_THROUGH_ENV if k in os.environ}
        env.update(self._env)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUNBUFFERED", "1")
        try:
            return subprocess.Popen(  # the command comes from the operator's config file, never from a request
                self._command,
                cwd=self._cwd,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise McpUnavailable(f"cannot start the MCP server ({type(exc).__name__})") from None

    def _send(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise McpUnavailable("the MCP server is not running")
        line = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            with self._write_lock:
                process.stdin.write(line)
                process.stdin.flush()
        except (OSError, ValueError):
            raise McpUnavailable("the MCP server's input is closed") from None

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for raw in process.stdout:
            try:
                message = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue  # not a protocol line; the server logs to stderr, so this should not happen
            if isinstance(message, dict):
                self._dispatch(message)
        self._fail_pending(McpLost("the MCP server closed its output"))

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for raw in process.stderr:
            self._stderr.append(raw.decode("utf-8", "replace").rstrip())

    def _dispatch(self, message: dict[str, Any]) -> None:
        if "method" in message:  # the server asks us something (ping) or tells us something (a notification)
            if "id" in message:
                answer: dict[str, Any] = (
                    {"jsonrpc": "2.0", "id": message["id"], "result": {}}
                    if message["method"] == "ping"
                    else {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "not supported"}}
                )
                try:
                    self._send(answer)
                except McpUnavailable:
                    pass
            return
        request_id = message.get("id")
        future = self._pending.pop(request_id, None) if isinstance(request_id, int) else None
        if future is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(_resolve, future, message)

    def _fail_pending(self, error: McpError) -> None:
        pending, self._pending = self._pending, {}
        if self._loop is None:
            return
        for future in pending.values():
            try:
                self._loop.call_soon_threadsafe(_reject, future, error)
            except RuntimeError:
                pass  # the loop is already closed (shutdown)


def _resolve(future: "Future[dict[str, Any]]", message: dict[str, Any]) -> None:
    if not future.done():
        future.set_result(message)


def _reject(future: "Future[dict[str, Any]]", error: McpError) -> None:
    if not future.done():
        future.set_exception(error)
