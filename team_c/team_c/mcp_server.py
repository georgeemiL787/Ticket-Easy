"""Local sandbox MCP server (stdio) for TEST-ONLY published artifacts.

LOCAL DEVELOPMENT ONLY, not production authentication: each process is bound at startup to one
test business and one test identity taken from the trusted connector configuration. Tool arguments
cannot choose a user or credential. Protocol messages use stdout; logs go to stderr.

    python -m team_c.mcp_server --business-id <id> --identity <name> [--database ...] [--connectors ...] [--sandbox-hosts ...]
"""
import argparse
import json
import logging
import sys
import anyio
import mcp_types as types
from mcp.server.lowlevel.server import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError
from mcp_types.jsonrpc import INVALID_PARAMS
from . import publishing
from .config import AppError, Settings
from .executor import load_connectors
from .service import Service
from .persistence.db import Store

log = logging.getLogger("team_c.mcp")


def result(envelope):
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(envelope))], structured_content=envelope,
                                is_error=envelope["status"] != "succeeded")


def build_server(service, business_id, identity):
    async def list_tools(ctx, params):
        tools = await anyio.to_thread.run_sync(service.published_tools, business_id, identity)
        return types.ListToolsResult(tools=[types.Tool(**t) for t in tools])

    async def call_tool(ctx, params):
        try:
            envelope = await anyio.to_thread.run_sync(service.invoke_published, business_id, identity, params.name, params.arguments)
        except AppError as exc:
            if exc.code == "tool_not_published":
                raise MCPError(INVALID_PARAMS, exc.message, {"code": exc.code})
            envelope = publishing.rejected(None, exc)
        except Exception:
            log.exception("tool call failed unexpectedly: %s", params.name)
            envelope = publishing.envelope(None, "error", error=dict(code="internal_error", message="The server failed while handling this call"),
                                           message="The server failed while handling this call; a write may or may not have been applied. Check the target before retrying.")
        log.info("call %s -> %s (execution %s)", params.name, envelope["status"], envelope["execution_id"])
        return result(envelope)

    return Server("team-c-sandbox", version="0.1.0", on_list_tools=list_tools, on_call_tool=call_tool,
                  instructions="TEST-ONLY sandbox tools compiled from owner-reviewed artifacts. The caller identity is fixed by this server; "
                               "results with is_error=true include partial and outcome_unknown executions, which must not be treated as success or retried blindly.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="TEST-ONLY sandbox MCP server over stdio (local development only)")
    parser.add_argument("--business-id", required=True)
    parser.add_argument("--identity", required=True, help="sandbox identity name in the trusted connector configuration")
    parser.add_argument("--database")
    parser.add_argument("--connectors")
    parser.add_argument("--sandbox-hosts")
    args = parser.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # request URLs can carry record references
    overrides = {k: v for k, v in dict(database_path=args.database, connectors_file=args.connectors, sandbox_hosts=args.sandbox_hosts).items() if v}
    settings = Settings(**overrides)
    service = Service(settings, Store(settings.database_path), None)
    service.store.one("SELECT id FROM businesses WHERE id=?", (args.business_id,))
    if not any(c.get("business_id") == args.business_id and args.identity in (c.get("identities") or {}) for c in load_connectors(settings).values()):
        parser.error("the identity is not configured for any connector of this business")
    log.info("serving business %s as identity %s from %s", args.business_id, args.identity, settings.database_path)
    server = build_server(service, args.business_id, args.identity)

    async def run():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    anyio.run(run)


if __name__ == "__main__":
    main()
