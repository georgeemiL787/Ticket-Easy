"""Start one TicketEasy service for the integrated local run (Team A, Team B, Team C, the Nile sandbox API).

    python integration/serve.py sandbox     # Nile Style sandbox shop API      http://127.0.0.1:8102  (Team B's venv)
    python integration/serve.py team-c      # Team C tool builder + REST API   http://127.0.0.1:8101  (team_c/.venv)
    python integration/serve.py team-a      # Team A safety + RAG              http://127.0.0.1:8001  (team_a/.venv)
    python integration/serve.py team-b      # Team B: chat, inbox, dashboard   http://127.0.0.1:8010  (team_b/.venv)

Each runs with its own team's virtualenv, working directory and an isolated database, so nothing here touches another
Team C instance (for example one on port 8000 with data/team_c.sqlite3). Local secrets this needs (the sandbox harness
key, Team C's session secret, Team B's cookie key) are generated once into integration/var/ (git-ignored). Team B
reads its Team C binding from integration/var/team_c.json, written by integration/team_c_lifecycle.py; without that
file Team B runs on its stand-in shop only. TEAM_B_MODE=live (the real Team A) unless --standin is given.
"""

import argparse
import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VAR = ROOT / "integration" / "var"
PORTS = {"sandbox": 8102, "team-c": 8101, "team-a": 8001, "team-b": 8010}


def venv_python(team: str) -> str:
    for candidate in (ROOT / team / ".venv" / "Scripts" / "python.exe", ROOT / team / ".venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    raise SystemExit(f"{team}/.venv is missing: create it first (see Docs/INTEGRATION.md)")


def secret(name: str) -> str:
    """A local value generated once and kept in integration/var/<name> (git-ignored), never printed."""
    VAR.mkdir(parents=True, exist_ok=True)
    path = VAR / name
    if not path.exists():
        path.write_text(secrets.token_hex(32), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


def command(service: str, standin: bool, no_auth: bool = False) -> tuple[list[str], Path, dict[str, str]]:
    env = dict(os.environ)
    port = str(PORTS[service])
    if service == "sandbox":
        env["NILE_SANDBOX_HARNESS_KEY"] = secret("sandbox_harness.key")
        args = ["-m", "uvicorn", "integration.nile_sandbox.app:create_app", "--factory", "--host", "127.0.0.1"]
        return [venv_python("team_b"), *args, "--port", port], ROOT, env
    if service == "team-c":
        env.update(
            DATABASE_PATH="data/integration.sqlite3",  # its own database, next to Team C's others (git-ignored)
            SANDBOX_HOSTS=f"127.0.0.1:{PORTS['sandbox']}",
            OPENAPI_FETCH_HOSTS=f"127.0.0.1:{PORTS['sandbox']}",
            SESSION_SECRET=secret("team_c_session.key"),
            PYTHONPATH=".",
        )
        args = ["-m", "uvicorn", "team_c.web:create_app", "--factory", "--host", "127.0.0.1"]
        return [venv_python("team_c"), *args, "--port", port], ROOT / "team_c", env
    if service == "team-a":
        env["PYTHONPATH"] = "src"
        args = ["-m", "uvicorn", "team_a.service:app", "--app-dir", "src", "--host", "127.0.0.1"]
        return [venv_python("team_a"), *args, "--port", port], ROOT / "team_a", env
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
        env["TEAM_B_TEAM_C_CONFIG"] = str(binding)
    args = ["-m", "uvicorn", "team_b.api.app:app", "--app-dir", "src", "--host", "127.0.0.1"]
    return [venv_python("team_b"), *args, "--port", port], ROOT / "team_b", env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("service", choices=sorted(PORTS))
    parser.add_argument("--standin", action="store_true", help="team-b: use Team B's stand-in safety/RAG, not Team A")
    parser.add_argument("--port", type=int, help="another port (e.g. a second team-b for looking at the pages)")
    parser.add_argument("--no-auth", action="store_true", help="team-b: sign-in off (TEAM_B_AUTH_REQUIRED=0); local only")
    args = parser.parse_args()
    if args.port:
        PORTS[args.service] = args.port
    cmd, cwd, env = command(args.service, args.standin, args.no_auth)
    if args.service == "team-b" and args.port:
        env["TEAM_B_DB_PATH"] = str(VAR / f"team_b_{args.port}.sqlite3")
    print(f"starting {args.service} on http://127.0.0.1:{PORTS[args.service]} (cwd {cwd})", flush=True)
    return subprocess.call(cmd, cwd=cwd, env=env)


if __name__ == "__main__":
    sys.exit(main())
