"""A stand-in for Team C's sandbox MCP server, for Team B's tests (Team B tests never start Team C's code).

It speaks the same stdio protocol (newline-delimited JSON-RPC: initialize, tools/list, tools/call) and plays a script
re-read on EVERY request, so a test can change what is published between two calls, the way Team C re-reads its
database. The script (path in FAKE_TEAM_C_SCRIPT):

    {"tools": [<MCP tool definitions>],
     "calls": {"<tool name>": <a structuredContent envelope> | {"rpc_error": {...}} | {"sleep": seconds} | {"exit": 1}},
     "refuse_identities": ["mallory"]}

A tool that is not in "tools" answers -32602 with data.code "tool_not_published", like Team C. Every call is appended
to FAKE_TEAM_C_LOG as one JSON line {identity, business_id, tool, arguments} so a test can see what was really sent.
Run: python tests/fake_team_c_mcp.py --business-id B --identity alice [--database ...]
"""

import argparse
import json
import os
import sys
import time


def script() -> dict:
    with open(os.environ["FAKE_TEAM_C_SCRIPT"], encoding="utf-8") as f:
        return json.load(f)


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--business-id", required=True)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--database")
    parser.add_argument("--connectors")
    parser.add_argument("--sandbox-hosts")
    args = parser.parse_args()
    if args.identity in script().get("refuse_identities", []):
        sys.stderr.write("the identity is not configured for any connector of this business\n")
        sys.exit(2)
    for line in sys.stdin:
        message = json.loads(line)
        method, request_id = message.get("method"), message.get("id")
        if request_id is None:
            continue  # a notification (initialized)
        if method == "initialize":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": message["params"]["protocolVersion"],
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fake-team-c", "version": "0"},
                    },
                }
            )
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": script()["tools"]}})
        elif method == "tools/call":
            name, arguments = message["params"]["name"], message["params"].get("arguments") or {}
            plan = script()
            log = os.environ.get("FAKE_TEAM_C_LOG")
            if log:
                with open(log, "a", encoding="utf-8") as f:
                    f.write(
                        json.dumps(
                            {
                                "identity": args.identity,
                                "business_id": args.business_id,
                                "tool": name,
                                "arguments": arguments,
                            }
                        )
                        + "\n"
                    )
            if name not in {t["name"] for t in plan["tools"]}:
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32602, "message": "not published", "data": {"code": "tool_not_published"}},
                    }
                )
                continue
            answer = plan["calls"].get(name, {})
            if "sleep" in answer:
                time.sleep(answer["sleep"])
                continue
            if "exit" in answer:
                sys.exit(answer["exit"])
            if "rpc_error" in answer:
                send({"jsonrpc": "2.0", "id": request_id, "error": answer["rpc_error"]})
                continue
            envelope = dict(answer)
            if envelope.get("echo_identity"):  # lets a test see which identity the call ran as
                envelope.pop("echo_identity")
                envelope["output"] = dict(envelope.get("output") or {}, ran_as=args.identity)
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(envelope)}],
                        "structuredContent": envelope,
                        "isError": envelope.get("status") != "succeeded",
                    },
                }
            )
        else:
            send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "unknown method"}})


if __name__ == "__main__":
    main()
