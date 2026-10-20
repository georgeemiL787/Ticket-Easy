"""Local MCP server (stdio) for published artifacts: sandbox publications, Live Demo publications that call the
owner-configured real API, and (a separate process) promoted PRODUCTION publications.

A normal process is bound at startup to one test business and one test identity taken from the trusted connector
configuration; tool arguments cannot choose a user or credential. A PRODUCTION process is started with --context-from-client:
it lists and runs only production tools, uses the business's service credential, and takes the verified customer's trusted
context (and an idempotency key) from the request's `_meta.ticketeasy`, never from arguments. TRUST BOUNDARY: that is safe only
because this stdio process is spawned by the calling server (Team B) and nobody else can write to its input; any network
transport would need mutual authentication first. A process without the flag ignores `_meta.ticketeasy` completely.
Protocol messages use stdout; logs go to stderr (never the context, only its hash is recorded by the tools service).

    python -m tools_service.mcp_server --business-id <id> --identity <name> [--database ...] [--connectors ...] [--sandbox-hosts ...]
    python -m tools_service.mcp_server --business-id <id> --identity service --context-from-client [...]
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


def client_context(params):
    """(context, idempotency_key) from `_meta.ticketeasy` of a production request; (None, None) when absent or malformed."""
    meta = params.meta if isinstance(getattr(params, "meta", None), dict) else {}
    found = meta.get("ticketeasy") if isinstance(meta.get("ticketeasy"), dict) else {}
    context = found.get("context")
    context = dict(context) if isinstance(context, dict) else None
    key = found.get("idempotency_key")
    return context, key if isinstance(key, str) and 8 <= len(key) <= 128 and key.isascii() and key.isprintable() else None


def build_server(service, business_id, identity, production=False):
    async def list_tools(ctx, params):
        tools = await anyio.to_thread.run_sync(service.published_tools, business_id, identity, production)
        return types.ListToolsResult(tools=[types.Tool(**t) for t in tools])

    async def call_tool(ctx, params):
        context, key = client_context(params) if production else (None, None)  # without the flag the metadata is never read
        try:
            envelope = await anyio.to_thread.run_sync(lambda: service.invoke_published(business_id, identity, params.name, params.arguments, context, key, production))
        except AppError as exc:
            if exc.code == "tool_not_published":
                raise MCPError(INVALID_PARAMS, exc.message, {"code": exc.code})
            envelope = publishing.rejected(None, exc)
        except Exception:
            log.exception("tool call failed unexpectedly: %s", params.name)
            envelope = publishing.envelope(None, "error", error=dict(code="internal_error", category="internal_error", message="The server failed while handling this call"),
                                           message="The server failed while handling this call; a write may or may not have been applied. Check the target before retrying.")
        log.info("call %s -> %s (execution %s)", params.name, envelope["status"], envelope["execution_id"])
        return result(envelope)

    return Server("team-c-sandbox", version="0.1.0", on_list_tools=list_tools, on_call_tool=call_tool,
                  instructions="Tools compiled from owner-reviewed artifacts: sandbox publications are sandbox-only; live_demo publications (see _meta.team_c.environment) "
                               "call the owner-configured real API and are not a production release. The caller identity is fixed by this server; "
                               "results with is_error=true include partial and outcome_unknown executions, which must not be treated as success or retried blindly.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="sandbox MCP server over stdio (local development only)")
    parser.add_argument("--business-id", required=True)
    parser.add_argument("--identity", required=True, help="sandbox identity name in the trusted connector configuration")
    parser.add_argument("--database")
    parser.add_argument("--connectors")
    parser.add_argument("--sandbox-hosts")
    parser.add_argument("--context-from-client", action="store_true", help="PRODUCTION process: service credential, customer context from _meta.ticketeasy")
    args = parser.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # request URLs can carry record references
    overrides = {k: v for k, v in dict(database_path=args.database, connectors_file=args.connectors, sandbox_hosts=args.sandbox_hosts).items() if v}
    settings = Settings(**overrides)
    service = Service(settings, Store(settings.database_path), None)
    service.store.one("SELECT id FROM businesses WHERE id=?", (args.business_id,))
    if args.context_from_client:
        if not settings.production_enabled:
            parser.error("production is switched off (TOOLS_PRODUCTION_ENABLED)")
        if not any(c.get("business_id") == args.business_id and c.get("environment") == "production" for c in load_connectors(settings).values()):
            parser.error("this business has no production connector")
        log.info("serving PRODUCTION tools of business %s with the service credential from %s", args.business_id, settings.database_path)
    else:
        if not any(c.get("business_id") == args.business_id and args.identity in (c.get("identities") or {}) for c in load_connectors(settings).values()):
            parser.error("the identity is not configured for any connector of this business")
        log.info("serving business %s as identity %s from %s", args.business_id, args.identity, settings.database_path)
    server = build_server(service, args.business_id, args.identity, production=args.context_from_client)

    async def run():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    anyio.run(run)


if __name__ == "__main__":
    main()
