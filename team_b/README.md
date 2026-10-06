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
3. In the chat, write `I want to talk to a human`. The chat says a colleague will reply. (The large-refund demo, where the
   agent opens the case by itself, is in the next section.)
4. In the inbox the case appears within 10 seconds (or reload). Type your name, click the case, read the briefing.
5. Click **Claim**, write a reply and press **Send reply**. It appears in the customer's chat at once.
6. Write another message in the chat: the agent stays quiet except for one "a colleague will reply" notice, and your inbox
   history shows the customer's message.
7. Click **Give back to assistant**. Ask the chat the return question again: the assistant answers again, remembering the
   conversation. (Or **Resolve** to close the case.)
8. Type a different name in the inbox: the claimed case now shows no action buttons, and a direct API call to reply as that
   name is refused with `409 INVALID_STATE`.
9. A case with a waiting action shows **Approve/Reject**; deciding re-checks the rules, runs the action once, and tells the customer in the chat (see the next section).

### The large-refund demo (the agent acts, a person approves)

A refund above EGP 3,000 may not be done by the assistant alone. This walks through the whole safe path.

1. `make run`, open the chat <http://127.0.0.1:8010/chat?tenant_id=shop_001> and the inbox <http://127.0.0.1:8010/inbox>.
2. In the chat write `I want a refund for order NS-20934`. The assistant asks for the phone number on the order.
3. Write `01098765405`. The assistant checks who you are and the rules, finds that the refund (EGP 3,450) needs approval, and
   says a colleague will review it. Nothing has been refunded yet.
4. In the inbox (within 10 seconds) a case appears with reason **approval_required**. Type your name, open it: the briefing
   shows the order facts, the rule that asked for a person (R-REFUND-LIMIT, refund_policy@v1#s3) and the waiting refund.
5. Click **Claim**, then **Approve**. The rules are checked again with the order as it is now, the refund runs exactly once,
   and the chat shows "Done! Your reference number is REF-...". **Reject** instead tells the customer politely and runs nothing.
6. Things to try: approve twice (the second does nothing); claim as another name and try to decide (refused with 409);
   change the order in the shop so the rules now say no, then approve (nothing runs, the case says why, the customer is told).

The same walk-through runs as an automated test: `tests/integration/test_sync1_demo.py`.

API: `GET /v1/handoff/cases?tenant_id=shop_001` and `POST /v1/handoff/cases/{id}/claim|release|reply|decision|resolve|return-to-agent`
with a body such as `{"agent": "Sara", "text": "Hello"}`.

## Measuring quality (`python -m team_b eval`)

`python -m team_b eval --set eval/conversations` runs 200 short conversations (50 each in English, Egyptian Arabic, mixed
Arabic-English and Arabizi) through the agent and writes `reports/eval_<date>.md` and `.json`: per style intent accuracy,
decision accuracy, citation accuracy, reply language match, handoff precision and recall, latency p50 and p95, the
conversations that went wrong most (with their first wrong turn) and a table by topic. `--llm` uses the configured AI model
for understanding. The gold labels come from the policies and rules (see `scripts/make_eval_set.py`), never from what the
agent does. `--save-baseline` records what the rules reach today in `eval/eval_baseline.json`; the test
`tests/integration/test_b_eval.py` keeps a 20-conversation sample at the targets (intent 0.90, decision 0.92, citation 0.95,
locale 1.0) or, until they are reached, at that baseline.

### Grading how replies read (`--judge`, optional)

With an AI model configured (`TEAM_B_LLM=ollama` or `openrouter`), `python -m team_b eval --judge` also asks the model to grade
each reply from 1 to 5 for clarity, politeness, register (does it sound like the customer's language and style) and
helpfulness, with a one-line reason each (rubric: `prompts/judge_v1.md`). The report shows the average per style. These grades
are advice: they never decide whether a test passes. Ten percent of the graded replies are written to
`reports/judge_spotcheck.csv`; a person fills in the `human_*` columns, then `python -m team_b judge-agreement reports/judge_spotcheck.csv`
prints how often the judge is exactly right and within one point.
