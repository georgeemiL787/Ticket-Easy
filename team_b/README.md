# Ticket-Easy · Team B

Team B builds the brain of Ticket-Easy: the service that receives every customer message (English, Egyptian Arabic, mixed or Arabizi), understands it, and decides exactly one outcome: answer, clarify, verify identity, ask for confirmation, execute an action, refuse, or hand off to a human. Every decision is recorded with its reasons in a decision trace.

It also owns the human side (handoff briefing, inbox, approvals) and the manager dashboard. Until Team A (policy search, rule checker) and Team C (shop actions) are ready, Team B runs against its own stand-ins and the demo shop "Nile Style", so the whole product can be built and tested without them. The product plan is in `Docs/plan/Ticket-Easy_Implementation_Plan.md`; project rules for contributors and Claude Code sessions are in `CLAUDE.md`.

## Install, test, run

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
make install        # pip install -e . -r requirements-dev.txt
make test           # pytest
make lint           # ruff check + ruff format --check + mypy
make run            # API on http://127.0.0.1:8010
```

Other targets: `make fmt` (auto-fix style), `make cov` (coverage), `make scenarios` (conversation scenarios).
Without `make`, run the commands from the `Makefile` directly.
