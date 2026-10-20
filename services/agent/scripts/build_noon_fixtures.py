"""Copy Team A's Noon (noon_eg) mock data into Team B's fixtures: Team B stays self-contained, tests repeatable.

Usage: python scripts/build_noon_fixtures.py [--source ../knowledge/data/e-commerce/noon_eg/seed]

Team A owns the data (customers, orders, returns). This writes fixtures/noon_eg/seed.json, which the stand-in shop loads
into a SQLite database (adapters/standins/sql_shop.py). Run it again whenever Team A updates the Noon data; the tools
(fixtures/noon_eg/tools.json) are Team B's and are not touched.
"""

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = ROOT.parent / "knowledge" / "data" / "e-commerce" / "noon_eg" / "seed"
OUT = ROOT / "fixtures" / "noon_eg" / "seed.json"


def build(source: Path) -> dict[str, object]:
    def read(name: str) -> dict[str, object]:
        return json.loads((source / name).read_text(encoding="utf-8"))  # type: ignore[no-any-return]

    customers, orders, returns = read("noon_customers.json"), read("noon_orders.json"), read("noon_returns.json")
    meta = orders["_meta"]
    return {
        "tenant_id": "noon_eg",
        "as_of": meta["as_of"],  # type: ignore[index]
        "source": "services/knowledge/data/e-commerce/noon_eg/seed (fictional people, phones and prices)",
        "customers": customers["customers"],
        "orders": orders["orders"],
        "returns": returns["returns"],
    }


def keywords_of(title: str) -> list[str]:
    """The words of a section title that help a search find it (keywords count more than body words)."""
    words = [w.lower() for w in re.findall(r"[A-Za-z]{4,}", title)]
    return sorted({w for w in words if w not in {"noon", "section"}}) + ["return"]


def policy_passages(source: Path) -> dict[str, object]:
    """The Noon return policy as passages numbered like Team A's index: one per "## " heading (…#s<n>)."""
    text = (source.parent / "corpus" / "noon_egypt_return_policy.md").read_text(encoding="utf-8")
    sections: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif sections and line.strip():
            sections[-1][1].append(line.strip())
    passages = [
        {
            "passage_id": f"noon_return_policy@v1#s{n}", "document_id": "noon_return_policy", "version": "v1",
            "section": title, "language": "en", "text": " ".join(body), "superseded": False,
            "keywords": keywords_of(title),
        }
        for n, (title, body) in enumerate(sections, start=1)
        if body
    ]  # fmt: skip
    documents = [
        {
            "document_id": "noon_return_policy",
            "version": "v1",
            "effective_date": "2026-10-01",
            "current": True,
            "language": "en",
        }
    ]
    return {"tenant_id": "noon_eg", "documents": documents, "passages": passages}


def write_stand_ins(folder: Path, source: Path) -> None:
    """What the offline stand-ins (policy search, safety screen, rule checker) need so Noon also runs without Team A:
    the policy passages, the same danger words as Nile Style, and NO approved rules (a change then goes to a person,
    exactly as Team A's rule checker answers a shop with no rules)."""
    shop = ROOT / "fixtures" / "shop_001"
    risk = json.loads((shop / "risk.json").read_text(encoding="utf-8-sig"))
    risk["tenant_id"] = "noon_eg"
    rules = json.loads((shop / "rules.json").read_text(encoding="utf-8-sig"))
    rules.update(
        {"tenant_id": "noon_eg", "rules": [], "description": "No approved rules for noon_eg yet: Team A writes them."}
    )
    empty = {"synonyms": {"entries": []}, "tickets": {"tickets": []}}
    for name, data in {
        "policies": policy_passages(source),
        "risk": risk,
        "rules": rules,
        "synonyms": {"tenant_id": "noon_eg", "description": "none yet", **empty["synonyms"]},
        "tickets": {"tenant_id": "noon_eg", "description": "none yet", **empty["tickets"]},
    }.items():
        text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
        (folder / f"{name}.json").write_text(text, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args()
    data = build(args.source)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    write_stand_ins(OUT.parent, args.source)
    counts = [len(data[k]) for k in ("customers", "orders", "returns")]  # type: ignore[arg-type]
    print(f"wrote {OUT}: {counts[0]} customers, {counts[1]} orders, {counts[2]} returns")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
