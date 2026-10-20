#!/usr/bin/env bash
# Regenerate the bulk noon_eg data in place and check it.
# Usage (from rag/):  bash data/e-commerce/noon_eg/scripts/rebuild.sh
# seed/ (hand-built) -> scripts/generate.py -> mock/ + tickets.jsonl -> db build -> scripts/verify.py
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
SERVICE_DIR=$(cd "$HERE/../../../.." && pwd)
python "$HERE/generate.py"
(cd "$SERVICE_DIR" && PYTHONPATH=src python -m knowledge_service db build --tenant noon_eg > /dev/null)
python "$HERE/verify.py" --db "$SERVICE_DIR/var/db/knowledge.sqlite"
