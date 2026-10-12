"""Start TicketEasy for a local run: Team A, Team B, Team C and the APIs they call.

    python integration/serve.py up          # check the setup, start everything in order, wait for health, report
    python integration/serve.py status      # which services answer, and the Live Demo settings
    python integration/serve.py <service>   # one service in the foreground: sandbox | demo-shop | team-c | team-a | team-b

Services (each with its own team's virtualenv, working directory, port and an isolated database):
    sandbox    Nile Style sandbox shop API (test data, TEST-ONLY)        http://127.0.0.1:8102
    demo-shop  a second, separate Nile shop API with its own credentials  http://127.0.0.1:8103
               (synthetic data on THIS machine; usable as a Live Demo target only through LIVE_DEMO_PRIVATE_HOSTS)
    team-c     Team C tool builder + REST API (+ its MCP server, started by Team B)  http://127.0.0.1:8101
    team-a     Team A safety + RAG                                       http://127.0.0.1:8001
    team-b     Team B: chat, inbox, dashboard                            http://127.0.0.1:8010

Configuration without editing source files:
- integration/live_demo.env (git-ignored; copy integration/live_demo.example.env): LIVE_DEMO_* settings for Team C
  and the credentials the Live Demo scripts store into Team C (never printed, never committed).
- integration/var/ (git-ignored): generated local keys, Team B's database, Team B's Team C binding (team_c.json,
  written by integration/team_c_lifecycle.py / live_demo_check.py). Without a binding Team B uses its stand-in shop.
Nothing here touches another Team C instance (for example one on port 8000 with data/team_c.sqlite3).
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

ROOT = Path(__file__).resolve().parents[1]
VAR = ROOT / "integration" / "var"
LIVE_ENV = ROOT / "integration" / "live_demo.env"
PRODUCTION_ENV = ROOT / "integration" / "production.env"
PORTS = {"sandbox": 8102, "demo-shop": 8103, "nile-prod": 8104, "team-c": 8101, "team-a": 8001, "team-b": 8010}
# nile-prod (a production-like shop deployment with a service token) starts only when integration/production.env exists
ORDER = ("sandbox", "demo-shop", *(("nile-prod",) if PRODUCTION_ENV.exists() else ()), "team-c", "team-a", "team-b")
TEAMS = {"sandbox": "team_b", "demo-shop": "team_b", "nile-prod": "team_b", "team-c": "team_c", "team-a": "team_a", "team-b": "team_b"}
LIVE_KEYS = ("LIVE_DEMO_ENABLED", "LIVE_DEMO_HOSTS", "LIVE_DEMO_PRIVATE_HOSTS", "LIVE_DEMO_CALLS_PER_MINUTE")
# the PRODUCTION_* lines of integration/production.env; the smoke settings belong to Team C's web app only
PRODUCTION_KEYS = ("PRODUCTION_ENABLED", "PRODUCTION_HOSTS", "PRODUCTION_PRIVATE_HOSTS", "PRODUCTION_CALLS_PER_MINUTE",
                   "PRODUCTION_CALLS_PER_CUSTOMER_PER_MINUTE")
SMOKE_KEYS = ("PRODUCTION_SMOKE_CONTEXT", "PRODUCTION_SMOKE_ARGUMENTS")


def venv_python(team: str) -> str | None:
    for candidate in (ROOT / team / ".venv" / "Scripts" / "python.exe", ROOT / team / ".venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    return None


def secret(name: str) -> str:
    """A local value generated once and kept in integration/var/<name> (git-ignored), never printed."""
    VAR.mkdir(parents=True, exist_ok=True)
    path = VAR / name
    if not path.exists():
        path.write_text(secrets.token_hex(32), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


def live_settings(path: Path = LIVE_ENV) -> dict[str, str]:
    """KEY=VALUE lines of integration/live_demo.env (or another env file; blank lines and # comments ignored)."""
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def command(service: str, standin: bool = False, no_auth: bool = False) -> tuple[list[str], Path, dict[str, str]]:
    python = venv_python(TEAMS[service])
    if python is None:
        raise SystemExit(f"{TEAMS[service]}/.venv is missing: create it first (Docs/INTEGRATION.md, 'Run it locally')")
    env = dict(os.environ)
    port = str(PORTS[service])
    if service in ("sandbox", "demo-shop", "nile-prod"):
        env["NILE_SANDBOX_HARNESS_KEY"] = secret({"sandbox": "sandbox_harness.key", "demo-shop": "demo_shop_harness.key", "nile-prod": "nile_prod_harness.key"}[service])
        if service == "demo-shop":
            env["NILE_SANDBOX_TITLE"] = "Nile Style demo shop (local deployment, synthetic data)"
        if service == "nile-prod":
            env["NILE_SANDBOX_TITLE"] = "Nile Style production deployment (LOCAL, synthetic data, service token)"
            env["NILE_SANDBOX_SERVICE_TOKEN"] = secret("prod_service.key")  # the one service credential; Team C reads the same file
        args = ["-m", "uvicorn", "integration.nile_sandbox.app:create_app", "--factory", "--host", "127.0.0.1"]
        return [python, *args, "--port", port], ROOT, env
    if service == "team-c":
        env.update(
            DATABASE_PATH="data/integration.sqlite3",  # its own database, next to Team C's others (git-ignored)
            SANDBOX_HOSTS=f"127.0.0.1:{PORTS['sandbox']}",
            OPENAPI_FETCH_HOSTS=f"127.0.0.1:{PORTS['sandbox']},127.0.0.1:{PORTS['demo-shop']}",
            SESSION_SECRET=secret("team_c_session.key"),
            NILE_SANDBOX_FIXTURE_KEY=secret("sandbox_harness.key"),  # the sandbox's fixture key defaults to its harness key
            PYTHONPATH=".",
        )
        env.update({k: v for k, v in live_settings().items() if k in LIVE_KEYS})
        if PRODUCTION_ENV.exists():
            production = live_settings(PRODUCTION_ENV)
            env.update({k: v for k, v in production.items() if k in PRODUCTION_KEYS + SMOKE_KEYS})
            env.update(PRODUCTION_AUDIT_DIR=str(VAR / "audit"), TEAM_C_ADMIN_TOKEN=secret("team_c_admin.key"))
        args = ["-m", "uvicorn", "team_c.web:create_app", "--factory", "--host", "127.0.0.1"]
        return [python, *args, "--port", port], ROOT / "team_c", env
    if service == "team-a":
        env["PYTHONPATH"] = "src"
        args = ["-m", "uvicorn", "team_a.service:app", "--app-dir", "src", "--host", "127.0.0.1"]
        return [python, *args, "--port", port], ROOT / "team_a", env
    env.update(
        TEAM_B_MODE="standin" if standin else "live",
        TEAM_B_TEAM_A_URL=f"http://127.0.0.1:{PORTS['team-a']}",
        TEAM_B_STORE="sqlite",
        TEAM_B_DB_PATH=str(VAR / "team_b.sqlite3"),
        TEAM_B_FIXED_TODAY="2026-09-28",  # the demo orders are dated against this day
        TEAM_B_AUTH_REQUIRED="0" if no_auth else "1",
        TEAM_B_SECRET_KEY=secret("team_b_cookie.key"),
        TEAM_B_LOG_JSON="0",
    )
    binding = VAR / "team_c.json"
    if binding.exists():
        sync_live_settings(binding)
        env["TEAM_B_TEAM_C_CONFIG"] = str(binding)
    if PRODUCTION_ENV.exists():
        env["TEAM_C_ADMIN_TOKEN"] = secret("team_c_admin.key")  # Team B's service credential TO Team C (admin actions)
    args = ["-m", "uvicorn", "team_b.api.app:app", "--app-dir", "src", "--host", "127.0.0.1"]
    return [python, *args, "--port", port], ROOT / "team_b", env


def sync_live_settings(binding: Path) -> None:
    """Team B starts Team C's MCP server itself; give that process the same LIVE_DEMO_* settings as Team C's web app
    (integration/live_demo.env is the one source). Without them Team C keeps every Live Demo tool switched off."""
    config = json.loads(binding.read_text(encoding="utf-8"))
    env = {k: v for k, v in (config.get("env") or {}).items() if k not in LIVE_KEYS + PRODUCTION_KEYS + ("PRODUCTION_AUDIT_DIR",)}
    env.update({k: v for k, v in live_settings().items() if k in LIVE_KEYS})
    if PRODUCTION_ENV.exists():  # the production MCP process Team B starts needs the same switches (never a secret: it reads the file)
        env.update({k: v for k, v in live_settings(PRODUCTION_ENV).items() if k in PRODUCTION_KEYS})
        env["PRODUCTION_AUDIT_DIR"] = str(VAR / "audit")
        config["admin_token_env"] = "TEAM_C_ADMIN_TOKEN"
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
          "team-c": "Ticket-Easy Team C",
          "team-a": "Ticket-Easy Team A"}


def ours(service: str) -> bool:
    """Is the program answering on this service's port really that TicketEasy service (not another program)?"""
    answer = health(service)
    if answer is None:
        return False
    if service == "team-b":
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
    for team in ("team_a", "team_b", "team_c"):
        if venv_python(team) is None:
            problems.append(f"ERROR   {team}/.venv is missing (Docs/INTEGRATION.md, 'Run it locally')")
    if not (ROOT / "team_a" / "var" / "index" / "shop_001").exists():
        problems.append("ERROR   Team A has no index for shop_001: cd team_a; PYTHONPATH=src .venv/Scripts/python -m team_a ingest --tenant shop_001 --no-embeddings")
    if not (ROOT / "team_b" / "web" / "dashboard" / "index.html").exists():
        problems.append("WARNING the dashboard is not built (cd dashboard; npm ci; npm run build): /dashboard will say so")
    if not (ROOT / "team_c" / ".env").exists():
        problems.append("WARNING team_c/.env is missing: Team C runs, but proposal generation needs a model provider (team_c/.env.example)")
    if not (VAR / "team_c.json").exists():
        problems.append("WARNING no Team C binding (integration/var/team_c.json): Team B serves every tool from its stand-in shop")
    live = live_settings()
    if live.get("LIVE_DEMO_ENABLED", "").lower() in ("1", "true", "yes") and not live.get("LIVE_DEMO_HOSTS"):
        problems.append("WARNING LIVE_DEMO_ENABLED is on but LIVE_DEMO_HOSTS is empty: no live connector can be saved")
    return problems


def up(standin: bool) -> int:
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
        cmd, cwd, env = command(service, standin)
        log = open(logs / f"{service}.log", "ab")  # noqa: SIM115 - kept open for the child's lifetime
        children.append((service, subprocess.Popen(cmd, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)))
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and health(service) is None:
            if children[-1][1].poll() is not None:
                print(f"ERROR   {service:<9} exited at start; see integration/var/logs/{service}.log")
                stop(children)
                return 1
            time.sleep(0.5)
        if health(service) is None:
            print(f"ERROR   {service:<9} did not become healthy in 90 s; see integration/var/logs/{service}.log")
            stop(children)
            return 1
        print(f"ok      {service:<9} http://127.0.0.1:{port}   (log: integration/var/logs/{service}.log)")
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
    b = health("team-b") or {}
    live = live_settings()
    print("\nTicketEasy")
    print(f"  Customer chat      http://127.0.0.1:{PORTS['team-b']}/chat?tenant_id=shop_001   (Team B mode: {b.get('mode', 'down')})")
    print(f"  Support inbox      http://127.0.0.1:{PORTS['team-b']}/inbox")
    print(f"  Business tools     http://127.0.0.1:{PORTS['team-b']}/dashboard/tools")
    print(f"  Tool builder       http://127.0.0.1:{PORTS['team-c']}/   (Team C)")
    enabled = live.get("LIVE_DEMO_ENABLED", "false").lower() in ("1", "true", "yes")
    print(f"  Live Demo          {'ON' if enabled else 'off'}; allowed hosts: {live.get('LIVE_DEMO_HOSTS') or 'none'}"
          f"; private-network exception: {live.get('LIVE_DEMO_PRIVATE_HOSTS') or 'none'}")


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
    parser.add_argument("--standin", action="store_true", help="team-b: use Team B's stand-in safety/RAG, not Team A")
    parser.add_argument("--port", type=int, help="another port (e.g. a second team-b for looking at the pages)")
    parser.add_argument("--no-auth", action="store_true", help="team-b: sign-in off (TEAM_B_AUTH_REQUIRED=0); local only")
    args = parser.parse_args()
    if args.service == "up":
        return up(args.standin)
    if args.service == "status":
        return status()
    if args.port:
        PORTS[args.service] = args.port
    cmd, cwd, env = command(args.service, args.standin, args.no_auth)
    if args.service == "team-b" and args.port:
        env["TEAM_B_DB_PATH"] = str(VAR / f"team_b_{args.port}.sqlite3")
    print(f"starting {args.service} on http://127.0.0.1:{PORTS[args.service]} (cwd {cwd})", flush=True)
    return subprocess.call(cmd, cwd=cwd, env=env)


if __name__ == "__main__":
    sys.exit(main())
