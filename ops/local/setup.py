"""One-time setup for a local run: virtualenvs, the knowledge index, the dashboard build and the settings files.

    python ops/local/setup.py            # everything below, skipping what is already done
    python ops/local/setup.py --check    # only report what is missing

What it does (each step is skipped when its result already exists):
  1. services/knowledge/.venv, services/agent/.venv (pip) and services/tools/.venv (uv sync --locked)
  2. the knowledge index for the demo shop (keyword search; no model needed)
  3. the dashboard build (apps/dashboard: npm ci, npm run build)
  4. services/agent/.env and services/tools/.env from their .env.example files, when missing

It never overwrites a settings file and never prints a key. What it cannot do for you is give the tools service a model
provider: put a Groq or OpenRouter key (or a running Ollama) in services/tools/.env, then run the two tool lifecycle commands
it prints at the end. Without them everything else works, and the agent uses its simulated shop.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WINDOWS = os.name == "nt"
BIN = "Scripts" if WINDOWS else "bin"
EXE = ".exe" if WINDOWS else ""


def python_of(folder: str) -> Path:
    return ROOT / folder / ".venv" / BIN / f"python{EXE}"


def run(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    print("  $", " ".join(command), f"   (in {cwd.relative_to(ROOT) or '.'})", flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True, shell=WINDOWS and command[0] in ("npm",))


def step(title: str, done: bool, action, check_only: bool) -> bool:
    if done:
        print(f"ok       {title}")
        return True
    if check_only:
        print(f"MISSING  {title}")
        return False
    print(f"doing    {title}", flush=True)
    action()
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="only report what is missing")
    check_only = parser.parse_args().check

    missing_tools = [name for name in ("uv", "npm") if shutil.which(name) is None]
    if missing_tools:
        print(f"Install first: {', '.join(missing_tools)}  (uv: https://docs.astral.sh/uv/ , npm comes with Node 20+)")
        return 1

    knowledge, agent, tools = ROOT / "services" / "knowledge", ROOT / "services" / "agent", ROOT / "services" / "tools"
    complete = True

    def knowledge_venv() -> None:
        subprocess.run([sys.executable, "-m", "venv", str(knowledge / ".venv")], check=True)
        run([str(python_of("services/knowledge")), "-m", "pip", "install", "-q", "-r", "requirements.txt"], knowledge)

    def agent_venv() -> None:
        subprocess.run([sys.executable, "-m", "venv", str(agent / ".venv")], check=True)
        run([str(python_of("services/agent")), "-m", "pip", "install", "-q", "-e", ".", "-r", "requirements-dev.txt"], agent)

    def knowledge_index() -> None:
        run([str(python_of("services/knowledge")), "-m", "knowledge_service", "ingest", "--tenant", "shop_001", "--no-embeddings"],
            knowledge, dict(os.environ, PYTHONPATH="src"))  # fmt: skip

    def dashboard() -> None:
        run(["npm", "ci"], ROOT / "apps" / "dashboard")
        run(["npm", "run", "build"], ROOT / "apps" / "dashboard")

    complete &= step("knowledge service environment", python_of("services/knowledge").exists(), knowledge_venv, check_only)
    complete &= step("agent environment", python_of("services/agent").exists(), agent_venv, check_only)
    complete &= step("tools service environment", python_of("services/tools").exists(), lambda: run(["uv", "sync", "--locked"], tools), check_only)
    if python_of("services/knowledge").exists() or not check_only:
        complete &= step("knowledge index for the demo shop", (knowledge / "var" / "index" / "shop_001").exists(), knowledge_index, check_only)
    complete &= step("dashboard build", (agent / "web" / "dashboard").exists() and any((agent / "web" / "dashboard").iterdir()), dashboard, check_only)

    for service in (agent, tools):
        target, example = service / ".env", service / ".env.example"
        if target.exists():
            print(f"ok       {target.relative_to(ROOT)}")
        elif check_only:
            print(f"MISSING  {target.relative_to(ROOT)}")
            complete = False
        else:
            shutil.copy(example, target)
            print(f"created  {target.relative_to(ROOT)} from its example (edit it to add your model provider)")

    print()
    if check_only:
        print("Everything is in place." if complete else "Run `python ops/local/setup.py` to fix what is MISSING.")
        return 0 if complete else 1
    print("Next:")
    print("  python ops/local/serve.py up                     # start everything (stop it with: serve.py down)")
    print("  # with a model provider in services/tools/.env, create the two demo tools once:")
    print(f"  {python_of('services/agent').relative_to(ROOT)} ops/local/tools_lifecycle.py --tool order")
    print(f"  {python_of('services/agent').relative_to(ROOT)} ops/local/tools_lifecycle.py --tool ticket")
    print("  python ops/local/serve.py down; python ops/local/serve.py up   # so the agent loads the new tools")
    return 0


if __name__ == "__main__":
    sys.exit(main())
