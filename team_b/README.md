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

## The support inbox (`/inbox`)

A browser page for support staff: a list of cases (most urgent first, then oldest), filters, and a case page with the
briefing (summary, customer and order facts, policy quotes, rule checker answers, actions tried, failures, the
conversation and the history). The buttons shown depend on the case: **Claim** on an open case; **Release**, **Reply**,
**Give back to assistant**, **Resolve** (and **Approve/Reject** when an action waits) only on a case you claimed. The list
refreshes every 10 seconds. Type your name at the top (it is remembered in the browser until logins exist).

### Try it by hand (a customer asks for a person, staff reply from the inbox)

1. `make run`, then open <http://127.0.0.1:8010/chat?tenant_id=shop_001> in one window and <http://127.0.0.1:8010/inbox> in another.
2. In the chat, write `How many days do I have to return an item?`. You get the return policy quoted with its source.
3. In the chat, write `I want a refund for order NS-20934` and then `I want to talk to a human`. (Until Track A's action flow
   is merged, the human request is the way to open a case. After SYNC 1 a refund above the limit opens an
   `approval_required` case by itself.) The chat says a colleague will reply.
4. In the inbox the case appears within 10 seconds (or reload). Type your name, click the case, read the briefing.
5. Click **Claim**, write a reply and press **Send reply**. It appears in the customer's chat at once.
6. Write another message in the chat: the agent stays quiet except for one "a colleague will reply" notice, and your inbox
   history shows the customer's message.
7. Click **Give back to assistant**. Ask the chat the return question again: the assistant answers again, remembering the
   conversation. (Or **Resolve** to close the case.)
8. Type a different name in the inbox: the claimed case now shows no action buttons, and a direct API call to reply as that
   name is refused with `409 INVALID_STATE`.
9. Approval of a waiting action (**Approve/Reject**) answers `501 NOT_IMPLEMENTED` until Track A builds `human_decide`.

API: `GET /v1/handoff/cases?tenant_id=shop_001` and `POST /v1/handoff/cases/{id}/claim|release|reply|decision|resolve|return-to-agent`
with a body such as `{"agent": "Sara", "text": "Hello"}`.
