import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAMES = (
    "DecisionTrace",
    "HandoffPackage",
    "HandoffCase",
    "AgentReply",
    "DashboardTenants",
    "DashboardOverview",
    "DashboardTimeseries",
    "DashboardConversationPage",
    "DashboardConversation",
    "DashboardEscalations",
    "DashboardAgent",
    "ReplayResult",
    "DashboardTools",
    "DashboardBusinessTools",
    "DashboardQueue",
    "DashboardPassage",
    "DashboardKnowledgeGaps",
    "TenantBundle",
)


def test_committed_schemas_match_the_models(tmp_path: Path) -> None:
    """If a model changes, run `python scripts/export_schemas.py` and commit the new schemas."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_schemas.py"), "--out", str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    for name in NAMES:
        generated = (tmp_path / f"{name}.schema.json").read_text(encoding="utf-8")
        committed = (ROOT / "contracts" / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8")
        assert generated == committed, f"{name}.schema.json is out of date"
        assert json.loads(generated)["title"] == name


def test_every_committed_schema_is_drift_checked() -> None:
    files = (ROOT / "contracts" / "schemas").glob("*.schema.json")
    committed = {path.name.removesuffix(".schema.json") for path in files}
    assert committed == set(NAMES), "add the schema to NAMES (or remove the stale file)"
