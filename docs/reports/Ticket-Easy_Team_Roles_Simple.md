# Ticket-Easy — Team Roles (simple version)

Five people. Each one owns one part of the agent, builds it, tests it, and demos it.

The one rule: **at any moment the whole thing must still demo end to end.** If you are about to break the demo, don't merge.

---

## Member 1 — Agent Brain

**You build:** the agent itself. It reads the customer's message and decides what to do.

**Your job, in order:**
1. Understand the message (Egyptian Arabic, English, or Arabizi) and reply in the same language.
2. For every message, decide five things and write them down: what the customer wants, which policy applies, what customer info is needed, what action to take, is the agent allowed to.
3. Verify the customer's identity before touching any data.
4. Call the other members' services (knowledge, tools, policy) and compose a reply that cites its source.
5. If the policy says no, the risk is high, or the agent is unsure, send the case to a human.
6. A simple web chat page. Keep it basic.

**You hand in:** the agent service, its prompts, the "decision trace" format, the chat page, 40 test conversations.

**Done means:** every reply shows its decision trace, and the agent never calls a tool the policy check didn't approve.

---

## Member 2 — Knowledge

**You build:** the part that finds the right passage in the company's documents so the agent never invents policy.

**Your job, in order:**
1. Load PDFs, Word, Excel and Markdown files, in Arabic and English, and split them into clean chunks.
2. Search that works with keywords **and** meaning (Arabizi breaks meaning-only search).
3. Return the exact passage, document and section so the agent can cite it.
4. Return "nothing found" clearly when the question isn't covered.
5. Also search past tickets.
6. Write the sample policies and FAQ for the demo company (Arabic + English).

**You hand in:** the ingestion tool, the knowledge search service, the demo documents, 50 test questions with the passage each should return.

**Done means:** the test questions hit the right passage at the agreed rate, and unknown questions come back empty instead of wrong.

---

## Member 3 — Policy & Safety

**You build:** the part that turns policy text into rules the agent cannot break.

**Your job, in order:**
1. Define what a rule looks like (condition, effect: allow / deny / needs a human, the policy sentence it came from).
2. Let the AI read policy documents and *propose* rules.
3. A human approves or rejects each proposed rule. Unapproved rules do nothing.
4. A strict checker: before any action runs, it answers allow, deny (with the reason and the policy sentence), or needs a human.
5. A detector for cases that always go to a human: fraud, regulator complaints, medical or safety issues, compensation claims, legal threats. Configurable per industry.

**You hand in:** the rule format, the rule extractor, the checker service, the always-escalate config, 40 test cases including customers trying to trick the agent.

**Done means:** in the tests, zero forbidden actions get through, and every "no" shows the policy sentence behind it.

---

## Member 4 — Tools & Systems

**You build:** the plug-and-play part. Give it a company's data schema, it produces the tools the agent can use.

**Your job, in order:**
1. Define what a tool looks like (name, read/create/update, inputs with types and required fields, permission level, risk level, on/off switch).
2. A generator: schema in (OpenAPI or database schema), tools out.
3. A server that serves those tools to the agent with no code changes when new ones are added.
4. Permissions: read = automatic, create = automatic after validation, update = needs a human unless whitelisted.
5. Check every input before running, and log every call (who, what, when, inputs, result).
6. Fake company systems for the demo: orders, returns, vouchers, tickets, customer profiles, with realistic Egyptian sample data. Later, a second fake company (telecom) to prove plug-and-play.

**You hand in:** the generator, the tools server, the fake systems and data, the action log.

**Done means:** a second company goes from schema to working tools in under an hour with no code changes, and every action is in the log.

---

## Member 5 — Handoff & Proof

**You build:** what happens when the agent stops, and the proof that the whole system works.

**Your job, in order:**
1. The handoff package: when a case goes to a human, an AI writes a summary, attaches the transcript, customer data, the policy passages used, what was already tried, why it was escalated, and a suggested next step.
2. The escalation queue: open, claimed, resolved, back to the agent.
3. The human inbox page: see cases, read the package, reply to the customer, approve or reject an action that needs a human.
4. The end-to-end test runner: scripted customer conversations, automatic checks (right tool called? blocked when it should be? escalated? cited?), plus an AI judge for answer quality. Produces the report we present.
5. The demo script for each milestone, with a reset button for the sample data.

**You hand in:** the handoff builder, the queue, the inbox page, the test runner and its report, the demo script.

**Done means:** someone who never saw the conversation can finish the case from the package alone, and the report covers every demo scenario.

---

## Everyone

- Keep your own tests green (30 to 50 cases, mixed Arabic / English / Arabizi).
- Provide a fake version of your service so others aren't blocked by you.
- Log everything to the shared log.
- Demo your part every week.

## How the parts connect

```
Customer chat
     |
 [1] Agent Brain
     |-- asks [2] Knowledge   for passages
     |-- asks [3] Policy      "am I allowed?"
     |-- calls [4] Tools      to read / create / update
     |
     '-- if not allowed / risky / unsure --> [5] Handoff --> Human inbox
```

## If time runs out

Stop adding features. Keep the last working demo, polish the demo script and the test report, and present that. The minimum great demo is: **the agent looks up an order, opens a ticket, gets blocked by the 14-day refund rule, and hands over to a human with the full package.**
