"""Serve Team B locally with the settings of team_b/.env (no Docker).

Usage: python scripts/run_server.py [port]      (default 8010)

Reads team_b/.env into the environment first (a variable already set in the shell wins), then starts uvicorn.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def load_env() -> None:
    for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


if __name__ == "__main__":
    load_env()
    import uvicorn

    uvicorn.run("team_b.api.app:app", app_dir=str(ROOT / "src"), port=int(sys.argv[1]) if len(sys.argv) > 1 else 8010)
