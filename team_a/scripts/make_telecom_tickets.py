"""Build data/telecom/telco_001/tickets.jsonl (past tickets for RAG) from the telecom support chats.

One ticket per distinct exchange: the customer's first message and the agent's final reply, from chats that
were resolved, refused or escalated (abandoned chats have no resolution). The earliest chat of each exchange
is kept. Every ticket passes the same personal-data check as stored precedents (is_safe_to_persist's
personal_data_reasons); an exchange that fails it is left out, never scrubbed.

Run from team_a/:  python scripts/make_telecom_tickets.py      (needs data/telecom/telco_001/telecom.db)
"""

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from team_a.config import settings  # noqa: E402
from team_a.knowledge.resolutions import personal_data_reasons  # noqa: E402
from team_a.schemas import ResolvedEscalationRequest  # noqa: E402
from team_a.text import normalize  # noqa: E402

TENANT = "telco_001"
OUTCOMES = ("resolved", "refused", "escalated")


def main() -> None:
    db = settings.tenant_dir(TENANT) / "telecom.db"
    if not db.exists():
        sys.exit(f"{db} not found. Generate it: python data/telecom/telco_001/scripts/generate.py")
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    rows = conn.execute(f"""
        SELECT cv.conversation_id, cv.intent, cv.outcome, cv.started_at,
          (SELECT text FROM conversation_messages m WHERE m.conversation_id = cv.conversation_id
             AND m.sender = 'customer' ORDER BY m.seq LIMIT 1),
          (SELECT text FROM conversation_messages m WHERE m.conversation_id = cv.conversation_id
             AND m.sender = 'agent' ORDER BY m.seq DESC LIMIT 1)
        FROM conversations cv WHERE cv.outcome IN ({','.join('?' * len(OUTCOMES))})
        ORDER BY cv.started_at, cv.conversation_id""", OUTCOMES).fetchall()

    tickets, seen, refused = [], set(), Counter()
    for conv_id, intent, outcome, started_at, customer, agent in rows:
        if not customer or not agent:
            continue
        key = (normalize(customer), normalize(agent))
        if key in seen:
            continue
        seen.add(key)
        reasons = personal_data_reasons(ResolvedEscalationRequest(
            request_id=conv_id, tenant_id=TENANT, category=intent, redacted_summary=customer.ljust(10),
            resolution=agent.ljust(5), escalation_reason="customer_request", risk_categories=[]))
        if reasons:
            refused.update(reasons)
            continue
        tickets.append({"ticket_id": f"TT-{conv_id.removeprefix('CV-')}", "tenant_id": TENANT, "category": intent,
                        "customer_message": customer, "resolution": agent, "created_at": started_at[:10],
                        "outcome": outcome})

    out = settings.tickets_file(TENANT)
    out.write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in tickets), encoding="utf-8", newline="\n")
    print(f"{len(rows)} chats -> {len(seen)} distinct exchanges -> {len(tickets)} tickets written to {out}")
    print("left out by the personal-data check:", dict(refused) or "none")
    print("by outcome:", dict(Counter(t["outcome"] for t in tickets)))


if __name__ == "__main__":
    main()
