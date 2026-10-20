"""Start TicketEasy for a local run: the knowledge service, the agent, the tools service and the APIs they call.

    python ops/local/serve.py up          # check the setup, start everything in order, wait for health, report
    python ops/local/serve.py status      # which services answer, and the Live Demo settings
    python ops/local/serve.py <service>   # one service in the foreground: sandbox | demo-shop | tools | knowledge | agent

Services (each with its own virtualenv, working directory, port and an isolated database):
    sandbox    Nile Style sandbox shop API (test data, TEST-ONLY)        http://127.0.0.1:8102
    demo-shop  a second, separate Nile shop API with its own credentials  http://127.0.0.1:8103
               (synthetic data on THIS machine; usable as a Live Demo target only through TOOLS_LIVE_DEMO_PRIVATE_HOSTS)
    tools     the tools service tool builder + REST API (+ its MCP server, started by the agent)  http://127.0.0.1:8101
    knowledge     the knowledge service safety + RAG                                       http://127.0.0.1:8001
    agent     the agent: chat, inbox, dashboard                            http://127.0.0.1:8010

Configuration without editing source files:
- ops/local/live_demo.env (git-ignored; copy ops/local/live_demo.example.env): LIVE_DEMO_* settings for the tools service
  and the credentials the Live Demo scripts store into the tools service (never printed, never committed).
- ops/local/var/ (git-ignored): generated local keys, the agent's database, the agent's the tools service binding (tools.json,
  written by ops/local/tools_lifecycle.py / live_demo_check.py). Without a binding the agent uses its simulated shop.
Nothing here touches another the tools service instance (for example one on port 8000 with data/tools.sqlite3).
"""

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VAR = ROOT / "ops" / "local" / "var"
LIVE_ENV = ROOT / "ops" / "local" / "live_demo.env"
PRODUCTION_ENV = ROOT / "ops" / "local" / "production.env"
PORTS = {"sandbox": 8102, "demo-shop": 8103, "nile-prod": 8104, "tools": 8101, "knowledge": 8001, "agent": 8010}
# nile-prod (a production-like shop deployment with a service token) starts only when ops/local/production.env exists
ORDER = ("sandbox", "demo-shop", *(("nile-prod",) if PRODUCTION_ENV.exists() else ()), "tools", "knowledge", "agent")
SERVICE_FOLDERS = {"sandbox": "services/agent", "demo-shop": "services/agent", "nile-prod": "services/agent", "tools": "services/tools", "knowledge": "services/knowledge", "agent": "services/agent"}
LIVE_KEYS = ("TOOLS_LIVE_DEMO_ENABLED", "TOOLS_LIVE_DEMO_HOSTS", "TOOLS_LIVE_DEMO_PRIVATE_HOSTS", "TOOLS_LIVE_DEMO_CALLS_PER_MINUTE")
# the PRODUCTION_* lines of ops/local/production.env; the smoke settings belong to the tools service's web app only
PRODUCTION_KEYS = ("TOOLS_PRODUCTION_ENABLED", "TOOLS_PRODUCTION_HOSTS", "TOOLS_PRODUCTION_PRIVATE_HOSTS", "TOOLS_PRODUCTION_CALLS_PER_MINUTE",
                   "TOOLS_PRODUCTION_CALLS_PER_CUSTOMER_PER_MINUTE")
SMOKE_KEYS = ("TOOLS_PRODUCTION_SMOKE_CONTEXT", "TOOLS_PRODUCTION_SMOKE_ARGUMENTS")


def venv_python(folder: str) -> str | None:
    for candidate in (ROOT / folder / ".venv" / "Scripts" / "python.exe", ROOT / folder / ".venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    return None


def secret(name: str) -> str:
    """A local value generated once and kept in ops/local/var/<name> (git-ignored), never printed."""
    VAR.mkdir(parents=True, exist_ok=True)
    path = VAR / name
    if not path.exists():
        path.write_text(secrets.token_hex(32), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


def live_settings(path: Path = LIVE_ENV) -> dict[str, str]:
    """KEY=VALUE lines of ops/local/live_demo.env (or another env file; blank lines and # comments ignored)."""
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def command(service: str, simulated: bool = False, no_auth: bool = False) -> tuple[list[str], Path, dict[str, str]]:
    python = venv_python(SERVICE_FOLDERS[service])
    if python is None:
        raise SystemExit(f"{SERVICE_FOLDERS[service]}/.venv is missing: create it first (docs/INTEGRATION.md, 'Run it locally')")
    env = dict(os.environ)
    port = str(PORTS[service])
    if service in ("sandbox", "demo-shop", "nile-prod"):
        env["NILE_SANDBOX_HARNESS_KEY"] = secret({"sandbox": "sandbox_harness.key", "demo-shop": "demo_shop_harness.key", "nile-prod": "nile_prod_harness.key"}[service])
        if service == "demo-shop":
            env["NILE_SANDBOX_TITLE"] = "Nile Style demo shop (local deployment, synthetic data)"
        if service == "nile-prod":
            env["NILE_SANDBOX_TITLE"] = "Nile Style production deployment (LOCAL, synthetic data, service token)"
            env["NILE_SANDBOX_SERVICE_TOKEN"] = secret("prod_service.key")  # the one service credential; the tools service reads the same file
        args = ["-m", "uvicorn", "ops.local.nile_sandbox.app:create_app", "--factory", "--host", "127.0.0.1"]
        return [python, *args, "--port", port], ROOT, env
    if service == "tools":
        env.update(
            TOOLS_DATABASE_PATH="data/integration.sqlite3",  # its own database, next to the tools service's others (git-ignored)
            TOOLS_SANDBOX_HOSTS=f"127.0.0.1:{PORTS['sandbox']}",
            TOOLS_OPENAPI_FETCH_HOSTS="loopback:*",  # any API running on this machine (a stack for local use only)
            TOOLS_SESSION_SECRET=secret("tools_session.key"),
            NILE_SANDBOX_FIXTURE_KEY=secret("sandbox_harness.key"),  # the sandbox's fixture key defaults to its harness key
            PYTHONPATH=".",
        )
        env.update({k: v for k, v in live_settings().items() if k in LIVE_KEYS})
        if PRODUCTION_ENV.exists():
            production = live_settings(PRODUCTION_ENV)
            env.update({k: v for k, v in production.items() if k in PRODUCTION_KEYS + SMOKE_KEYS})
            env.update(TOOLS_PRODUCTION_AUDIT_DIR=str(VAR / "audit"), TOOLS_ADMIN_TOKEN=secret("tools_admin.key"))
        args = ["-m", "uvicorn", "tools_service.web:create_app", "--factory", "--host", "127.0.0.1"]
        return [python, *args, "--port", port], ROOT / "services" / "tools", env
    if service == "knowledge":
        env["PYTHONPATH"] = "src"
        env["KNOWLEDGE_ADMIN_API_KEY"] = secret("knowledge_admin.key")  # the knowledge pages (documents, index, rules) go through it
        env["KNOWLEDGE_SERVICE_API_KEY"] = secret("knowledge_service.key")  # only a caller that sends it may search or check actions
        args = ["-m", "uvicorn", "knowledge_service.api:app", "--app-dir", "src", "--host", "127.0.0.1"]
        return [python, *args, "--port", port], ROOT / "services" / "knowledge", env
    # The agent reads only the process environment, so load its own .env here (AI model, per-job models, agent mode).
    # A variable already set in the shell wins, and the fixed values below (mode, database, keys) win over the file.
    for key, value in live_settings(ROOT / "services" / "agent" / ".env").items():
        if key.startswith("AGENT_") and key not in os.environ and value:
            env[key] = value.split(" #", 1)[0].strip()
    env.update(
        AGENT_BACKENDS="simulated" if simulated else "live",
        AGENT_KNOWLEDGE_URL=f"http://127.0.0.1:{PORTS['knowledge']}",
        AGENT_KNOWLEDGE_KEY=secret("knowledge_service.key"),
        AGENT_KNOWLEDGE_ADMIN_KEY=secret("knowledge_admin.key"),
        AGENT_STORE="sqlite",
        AGENT_DB_PATH=str(VAR / "agent.sqlite3"),
        AGENT_FIXED_TODAY="2026-09-28",  # the demo orders are dated against this day
        AGENT_CLOCK_TICKS="1",  # ... but the clock keeps running, so a new case is "just now", not 15 days old
        AGENT_AUTH_REQUIRED="0" if no_auth else "1",
        AGENT_SECRET_KEY=secret("agent_cookie.key"),
        AGENT_LOG_JSON="0",
    )
    binding = VAR / "tools.json"
    if binding.exists():
        sync_live_settings(binding)
        env["AGENT_TOOLS_CONFIG"] = str(binding)
    if PRODUCTION_ENV.exists():
        env["TOOLS_ADMIN_TOKEN"] = secret("tools_admin.key")  # The agent's service credential TO the tools service (admin actions)
    args = ["-m", "uvicorn", "agent_service.api.app:app", "--app-dir", "src", "--host", "127.0.0.1"]
    return [python, *args, "--port", port], ROOT / "services" / "agent", env


def sync_live_settings(binding: Path) -> None:
    """The agent starts the tools service's MCP server itself; give that process the same LIVE_DEMO_* settings as the tools service's web app
    (ops/local/live_demo.env is the one source). Without them the tools service keeps every Live Demo tool switched off."""
    config = json.loads(binding.read_text(encoding="utf-8"))
    env = {k: v for k, v in (config.get("env") or {}).items() if k not in LIVE_KEYS + PRODUCTION_KEYS + ("TOOLS_PRODUCTION_AUDIT_DIR",)}
    env.update({k: v for k, v in live_settings().items() if k in LIVE_KEYS})
    if PRODUCTION_ENV.exists():  # the production MCP process the agent starts needs the same switches (never a secret: it reads the file)
        env.update({k: v for k, v in live_settings(PRODUCTION_ENV).items() if k in PRODUCTION_KEYS})
        env["TOOLS_PRODUCTION_AUDIT_DIR"] = str(VAR / "audit")
        config["admin_token_env"] = "TOOLS_ADMIN_TOKEN"
    if env != config.get("env") or PRODUCTION_ENV.exists():
        config["env"] = env
        binding.write_text(json.dumps(config, indent=2), encoding="utf-8")


def health(service: str) -> dict | None:
    """The service's /health answer, or None when nothing answers there."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORTS[service]}/health", timeout=2) as r:
            return json.loads(r.read() or b"{}")
    except (urllib.error.URLError, OSError, ValueError):
        return None


TITLES = {"sandbox": "Nile Style sandbox shop", "demo-shop": "Nile Style demo shop", "nile-prod": "Nile Style production deployment",
          "tools": "Ticket-Easy tools",
          "knowledge": "Ticket-Easy knowledge service"}


def ours(service: str) -> bool:
    """Is the program answering on this service's port really that TicketEasy service (not another program)?"""
    answer = health(service)
    if answer is None:
        return False
    if service == "agent":
        return "mode" in answer and "tenants" in answer
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORTS[service]}/openapi.json", timeout=2) as r:
            return str(json.loads(r.read()).get("info", {}).get("title", "")).startswith(TITLES[service])
    except (urllib.error.URLError, OSError, ValueError):
        return False


def port_taken(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def preflight() -> list[str]:
    """Problems that stop a service, and warnings that limit what works; nothing secret is printed."""
    problems = []
    for folder in ("services/knowledge", "services/agent", "services/tools"):
        if venv_python(folder) is None:
            problems.append(f"ERROR   {folder}/.venv is missing (docs/INTEGRATION.md, 'Run it locally')")
    if not (ROOT / "services" / "knowledge" / "var" / "index" / "shop_001").exists():
        problems.append("ERROR   the knowledge service has no index for shop_001: cd services/knowledge; PYTHONPATH=src .venv/Scripts/python -m knowledge_service ingest --tenant shop_001 --no-embeddings")
    if not (ROOT / "services" / "agent" / "web" / "dashboard" / "index.html").exists():
        problems.append("WARNING the dashboard is not built (cd apps/dashboard; npm ci; npm run build): /dashboard will say so")
    if not (ROOT / "services" / "tools" / ".env").exists():
        problems.append("WARNING services/tools/.env is missing: the tools service runs, but proposal generation needs a model provider (services/tools/.env.example)")
    if not (VAR / "tools.json").exists():
        problems.append("WARNING no the tools service binding (ops/local/var/tools.json): the agent serves every tool from its simulated shop")
    live = live_settings()
    if live.get("TOOLS_LIVE_DEMO_ENABLED", "").lower() in ("1", "true", "yes") and not live.get("TOOLS_LIVE_DEMO_HOSTS"):
        problems.append("WARNING TOOLS_LIVE_DEMO_ENABLED is on but TOOLS_LIVE_DEMO_HOSTS is empty: no live connector can be saved")
    return problems


def up(simulated: bool) -> int:
    issues = preflight()
    for line in issues:
        print(line)
    if any(line.startswith("ERROR") for line in issues):
        return 1
    children: list[tuple[str, subprocess.Popen]] = []
    logs = VAR / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    for service in ORDER:
        port = PORTS[service]
        if ours(service):
            print(f"ok      {service:<9} already running on http://127.0.0.1:{port} (reused, not started twice)")
            continue
        if port_taken(port):
            print(f"ERROR   {service:<9} port {port} is used by another program (not TicketEasy's {service}); stop it or free the port")
            stop(children)
            return 1
        cmd, cwd, env = command(service, simulated)
        log = open(logs / f"{service}.log", "ab")  # noqa: SIM115 - kept open for the child's lifetime
        children.append((service, subprocess.Popen(cmd, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)))
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and health(service) is None:
            if children[-1][1].poll() is not None:
                print(f"ERROR   {service:<9} exited at start; see ops/local/var/logs/{service}.log")
                stop(children)
                return 1
            time.sleep(0.5)
        if health(service) is None:
            print(f"ERROR   {service:<9} did not become healthy in 90 s; see ops/local/var/logs/{service}.log")
            stop(children)
            return 1
        print(f"ok      {service:<9} http://127.0.0.1:{port}   (log: ops/local/var/logs/{service}.log)")
    report()
    if not children:
        return 0
    print("\nRunning. Press Ctrl+C to stop the services started here.")
    try:
        while all(p.poll() is None for _, p in children):
            time.sleep(1)
        print("A service stopped: " + ", ".join(s for s, p in children if p.poll() is not None) + "; stopping the rest.")
    except KeyboardInterrupt:
        pass
    stop(children)
    return 0


def stop(children: list[tuple[str, subprocess.Popen]]) -> None:
    for _, p in reversed(children):
        if p.poll() is None:
            p.terminate()
    for _, p in children:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()


def report() -> None:
    b = health("agent") or {}
    live = live_settings()
    print("\nTicketEasy")
    print(f"  Customer chat      http://127.0.0.1:{PORTS['agent']}/chat?tenant_id=shop_001   (the agent mode: {b.get('mode', 'down')})")
    print(f"  Support inbox      http://127.0.0.1:{PORTS['agent']}/inbox")
    print(f"  Business tools     http://127.0.0.1:{PORTS['agent']}/dashboard/tools")
    print(f"  Tool builder       http://127.0.0.1:{PORTS['tools']}/   (the tools service)")
    enabled = live.get("TOOLS_LIVE_DEMO_ENABLED", "false").lower() in ("1", "true", "yes")
    print(f"  Live Demo          {'ON' if enabled else 'off'}; allowed hosts: {live.get('TOOLS_LIVE_DEMO_HOSTS') or 'none'}"
          f"; private-network exception: {live.get('TOOLS_LIVE_DEMO_PRIVATE_HOSTS') or 'none'}")


def status() -> int:
    for service in ORDER:
        state = "ok" if ours(service) else "other" if port_taken(PORTS[service]) else "down"
        print(f"{state:<7} {service:<9} http://127.0.0.1:{PORTS[service]}" + ("   (another program holds this port)" if state == "other" else ""))
    report()
    for line in preflight():
        print(line)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("service", choices=["up", "status", *ORDER])
    parser.add_argument("--simulated", action="store_true", help="agent: use the agent's simulated safety/RAG, not the knowledge service")
    parser.add_argument("--port", type=int, help="another port (e.g. a second agent for looking at the pages)")
    parser.add_argument("--no-auth", action="store_true", help="agent: sign-in off (AGENT_AUTH_REQUIRED=0); local only")
    args = parser.parse_args()
    if args.service == "up":
        return up(args.simulated)
    if args.service == "status":
        return status()
    if args.port:
        PORTS[args.service] = args.port
    cmd, cwd, env = command(args.service, args.simulated, args.no_auth)
    if args.service == "agent" and args.port:
        env["AGENT_DB_PATH"] = str(VAR / f"agent_{args.port}.sqlite3")
    print(f"starting {args.service} on http://127.0.0.1:{PORTS[args.service]} (cwd {cwd})", flush=True)
    return subprocess.call(cmd, cwd=cwd, env=env)


if __name__ == "__main__":
    sys.exit(main())
