# Agent mode against today's flow (U6.3)

Run on 2026-10-10 with the real Groq models (orchestrator `gpt-oss-120b`; reviewer, knowledge and writer on `qwen3.8-27b`
with `gpt-oss-20b` as backup; reader, summary and judge on `gpt-oss-20b`). Every model is on the free plan:
30 requests/min, 8K tokens/min, 200K tokens/day.

**This is a small sample, not the full set.** The free plan's daily token cap does not allow the 240-conversation set three
times, so each run uses 4 conversations per language style (16 conversations, 24 messages, spread over different topics),
and the shadow run uses 1 per style. The numbers show direction, not proof; one conversation moves a percentage by 4 to 8
points. Nile-Chat (Egyptian writer) was left out so the timings only reflect the Groq models.

Runs: `off_ai` (agent mode off, AI on), `agent_on` (the first agent run), `agent_on_v2` (after two small fixes made because
of the first run: asking for a phone number is labelled "verify identity" as in today's flow, and an out-of-scope reply is
a refusal), `shadow` (the agent only watches). Reports: `reports/eval_<label>_2026-10-10.md/.json`.

## Table (off_ai against agent_on_v2, all 16 conversations)

| measure | target | today's flow + AI | agent on | holds? |
|---|---|---|---|---|
| solved without a person | higher | 87.5% | 87.5% | equal |
| wrong or unapproved actions | 0 | 0 | 0 | **yes** |
| unbacked facts reaching the customer | 0 | 0 | 0 | **yes** |
| needless handoffs | lower | 0 | 0 | equal |
| must-escalate cases escalated | 100% | 50% (1 of 2) | 50% (1 of 2) | **no (both)**: the same case is missed |
| reply quality (AI judge, 1-5) | higher | 4.11 | 3.97 | no (-0.15) |
| intent accuracy | | 93.8% | 100.0% | better |
| decision accuracy | | 87.5% | 70.8% | no (-16.7 pts) |
| AI calls per message | lower | 2.62 | 4.62 | no (+2.0) |
| time per reply, median | lower | 2.6 s | 22 s | no |
| time per reply, p95 | lower | 47 s | 41 s | similar |

Shadow run (4 conversations): the agent was asked on 3 turns and its first move agreed with today's flow on 3 (100%).

Fallback rate (agent loop gave up and the usual flow answered): 0 of 24 messages in `agent_on_v2` (1 unusable model answer
in the first run, repaired by the retry). Reasons seen: none; no rate-limit fallback happened because the limiter waited
instead (see time).

## Reading it

- **Safety holds in both modes:** nothing wrong was done, nothing unbacked was said.
- **Slower and more calls:** the agent asks the orchestrator at least once per message on top of the reader. The 22 s median
  is mostly the limiter waiting: the orchestrator prompt is about 3,500 tokens and the plan allows 8K tokens a minute, so
  roughly two calls a minute. On a paid plan this would be a few seconds.
- **Decision accuracy is lower** in the sample: the agent answers or asks a different question in 7 of 24 messages (for example
  an out-of-scope message answered instead of asked about; a policy question not found is offered a colleague at once instead
  of asking to rephrase first; two order-status messages ask for the phone as a plain question).

## Decision: agent mode stays OFF by default

Not every target holds (decision accuracy, reply quality, time and calls are worse; escalation recall is equal but not 100%).
`TEAM_B_AGENT_MODE` keeps its current default and `.env.example` is unchanged.

Smallest changes that would most likely fix it, to be re-measured:
1. Make the orchestrator prompt shorter (send only the tools the message needs): fewer tokens, so less waiting under the limit.
2. Label two more asks like today's flow (order id asked while verifying; a first unanswerable policy question asks to rephrase
   before offering a person), as was done for the phone number.
3. Run the full 240 conversations on a plan with higher limits, three times, before any switch.
