"""Real check of the tools service's automatic tests against the Nile sandbox shop (TEST-ONLY, needs a model provider for the tools service).

For every Nile tool (order, ticket, orders, return, exchange, refund, cancel, address, voucher) it drives the real lifecycle
(ops/local/tools_lifecycle.py: business -> model proposal -> scripted, labelled owner approval -> build) and then ONE
automatic-test call, with no hand-written test identities, order ids or scenarios. It passes when each tool reaches
ready_to_publish (the lifecycle then publishes it to the sandbox) and every evidence test of the artifact is an
`auto.*` test.

Needs the sandbox and the tools service running (ops/local/serve.py up):

    services/agent/.venv/Scripts/python.exe ops/local/autotest_check.py [--fresh] [--tool order ...]
"""

import argparse
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops" / "local"))
import tools_lifecycle as lifecycle  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fresh", action="store_true", help="start every tool over (the order tool creates a new business in the tools service, the others join it)")
    parser.add_argument("--tool", action="append", choices=sorted(lifecycle.TOOLS), help="only these tools (default: all)")
    parser.add_argument("--variant", default="", help="pass --variant to the lifecycle (a new proposal instead of an earlier stuck one)")
    args = parser.parse_args()
    tools = args.tool or sorted(lifecycle.TOOLS, key=lambda t: list(lifecycle.TOOLS).index(t))
    failures: list[str] = []
    for n, tool in enumerate(tools):
        print(f"\n##### {tool}", flush=True)
        cmd = [sys.executable, str(ROOT / "ops" / "local" / "tools_lifecycle.py"), "--tool", tool]
        if args.fresh:  # every tool starts over; the first one (order) creates the business the others share
            cmd.append("--fresh")
        if args.variant:
            cmd += ["--variant", args.variant]
        done = subprocess.run(cmd, cwd=ROOT)
        if done.returncode != 0:
            failures.append(f"{tool}: the lifecycle stopped (see above)")
            continue
        state = lifecycle.load_state(False, tool)
        artifact = httpx.get(f"{lifecycle.API}/artifacts/{state['artifact_id']}", timeout=60, trust_env=False).json()
        evidence = [t for t in artifact["tests"] if t["current"] and t["verdict"] == "passed"]
        manual = [t["name"] for t in evidence if not t["name"].startswith("auto.")]
        if artifact["readiness_state"] != "published":
            failures.append(f"{tool}: readiness_state is {artifact['readiness_state']}")
        elif manual or not evidence:
            failures.append(f"{tool}: evidence is not all automatic ({manual or 'none'})")
        else:
            print(f"OK {tool}: {len(evidence)} automatic evidence tests, state {artifact['readiness_state']}")
    print("\n" + ("AUTOTEST CHECK PASSED" if not failures else "AUTOTEST CHECK FAILED\n- " + "\n- ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
