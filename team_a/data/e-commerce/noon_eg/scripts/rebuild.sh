#!/usr/bin/env bash
# Regenerate the bulk noon_eg data in place and check it.
# Usage (from team_a/):  bash data/e-commerce/noon_eg/scripts/rebuild.sh
# seed/ (hand-built) -> scripts/generate.py -> mock/ + tickets.jsonl -> db build -> scripts/verify.py
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
TEAM_A=$(cd "$HERE/../../../.." && pwd)
python "$HERE/generate.py"
(cd "$TEAM_A" && PYTHONPATH=src python -m team_a db build --tenant noon_eg > /dev/null)
python "$HERE/verify.py" --db "$TEAM_A/var/db/team_a.sqlite"
