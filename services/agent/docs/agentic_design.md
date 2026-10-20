# The agentic upgrade, in plain language

(For the presentation. Numbers are from `docs/agent_comparison.md`, a small sample run with the real Groq models.)

## The idea

Before, the assistant followed a fixed path for every message: understand, check safety, find the request, run its handler.
Now it can also work like a person at a help desk: **make a plan, take a step, look at what happened, change the plan**.
An AI model, the **planner**, chooses the next step. **Everything it chooses is checked by code before it happens.**

## Three agents and four helpers

Agents (they decide something):
1. **Orchestrator**: plans the message and proposes one move at a time: look up an order, search the policy, propose an
   action (a refund, a return...), ask the customer one question, hand over to a person, or reply.
2. **Knowledge agent**: writes the policy search, searches again if the first try finds nothing, and says "not found"
   instead of guessing. Policy answers are always quoted word for word from the documents.
3. **Reviewer**: a second pair of eyes on every written reply and every write action. It can only make things stricter
   (send a case to a person, block a reply), never looser.

Helpers (they do one small job):
- **Reader**: understands the message (what is wanted, which language, how upset).
- **Writer**: words the reply from a plan, in the customer's language and tone (checked so it adds no new number or promise).
- **Egyptian writer** (optional): Nile-Chat, a model trained on Egyptian Arabic, for Arabic and Arabizi replies.
- **Summary** and **judge**: the handoff summary for the person who takes over, and the quality grader used in tests.

## Plan, act, re-plan

For "where is my order and cancel it", the plan has two steps. The planner looks up the order (first step), answers the
status, then proposes the cancellation (second step). The customer's yes is still needed; the plan waits for it and continues
afterwards. If the customer changes their mind or the shop says no, the plan changes, and the dashboard shows each plan and
each move.

## What code still decides (and always will)

- The gates in front of every change: the right customer, the rule checker's yes (or a person's approval), the customer's
  own "yes" in a later message, and a check that the shop really did it.
- **Forced handoffs happen at once, with no AI in the way:** a safety flag, a request for a person, a case a person already
  owns, a rule that needs a human, an order that is not the customer's, failed identity, a repeated refusal.
- The agent may *choose* to hand over only after trying (frustration still high after an apology, a policy answer not found
  twice, an outage after a retry, an unclear request after two questions), and code checks it really tried.
- The AI may not start an action the customer did not ask for, use an order number nobody gave, cite a source it did not
  retrieve, or say a number or date that is not in the facts. Anything that fails a check is refused, and two refusals in a
  row send the message to today's flow.
- Attack tests (a model that does whatever it is told, a hidden instruction in data, endless loops, huge inputs) found and
  closed three gaps; all pass.

## How it did (small sample, 16 conversations; free plan)

| | today's flow + AI | agent mode |
|---|---|---|
| wrong or unapproved actions | 0 | 0 |
| unbacked facts reaching customers | 0 | 0 |
| understanding the request | 94% | 100% |
| the right outcome | 88% | 83% |
| reply quality (judge, 1-5) | 4.1 | 3.9 |
| AI calls per message | 2.6 | 5.3 |
| median time per reply | 2.6 s | 61 s (waiting for the free plan's token limit) |
| gave up and used today's flow | n/a | 2 of 24 messages |

**Decision:** agent mode is built, safe, observable and switchable per shop, but **stays off by default** until the outcome
accuracy and the cost per message match today's flow. The next steps are listed in `docs/agent_comparison.md`.

## What the managers see

The conversation page shows each message's plan and moves with the checks that passed or failed; the Overview shows how often
the agent gave up (and why), how many replies the reviewer stopped, moves per message and who chose each handoff; the
Escalations page lists which request types most often end with a person, so the team knows what to automate next. An admin
can replay a past conversation with other settings and compare the two runs.
