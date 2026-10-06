"""Write JSON Schemas of the brain public models to contracts/schemas/.

Usage: python scripts/export_schemas.py [--out DIR]
"""

import argparse
import json
from pathlib import Path

from pydantic import BaseModel

from team_b.api import dashboard
from team_b.domain.handoff import HandoffCase, HandoffPackage
from team_b.domain.reply import AgentReply
from team_b.domain.trace import DecisionTrace

ROOT = Path(__file__).resolve().parent.parent
MODELS: tuple[type[BaseModel], ...] = (
    DecisionTrace,
    HandoffPackage,
    HandoffCase,
    AgentReply,
    dashboard.DashboardTenants,
    dashboard.DashboardOverview,
    dashboard.DashboardTimeseries,
    dashboard.DashboardConversationPage,
    dashboard.DashboardConversation,
    dashboard.DashboardEscalations,
    dashboard.DashboardTools,
    dashboard.DashboardQueue,
    dashboard.DashboardPassage,
    dashboard.DashboardKnowledgeGaps,
)


def render(model: type[BaseModel]) -> str:
    return json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "contracts" / "schemas")
    out: Path = parser.parse_args(argv).out
    out.mkdir(parents=True, exist_ok=True)
    for model in MODELS:
        path = out / f"{model.__name__}.schema.json"
        path.write_bytes(render(model).encode("utf-8"))
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
