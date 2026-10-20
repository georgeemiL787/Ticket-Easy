# Agent mode against today's flow (U6.3)

Run on 2026-10-10 with the real Groq models (planner `gpt-oss-120b`; reviewer, knowledge and writer on `qwen3.8-27b`
with `gpt-oss-20b` as backup; reader, summary and judge on `gpt-oss-20b`). Every model is on the free plan:
30 requests/min, 8K tokens/min, 200K tokens/day.

**This is a small sample, not the full set.** The free plan's daily token cap does not allow the 240-conversation set three
times, so each run uses 4 conversations per language style (16 conversations, 24 messages, spread over different topics),
and the shadow run uses 1 per style. The numbers show direction, not proof; one conversation moves a percentage by 4 to 8
points. Nile-Chat (Egyptian writer) was left out so the timings only reflect the Groq models.

Runs: `off_ai` (agent mode off, AI on), `agent_on_v3` (agent mode on, the run in the table), `shadow` (the agent only watches).
Two earlier agent runs (`agent_on`, `agent_on_v2`) were thrown away as a measure of the agent: the free plan makes a second
planner call wait about a minute (8K tokens a minute against a 3,500-token prompt), the agent loop's 20-second turn limit
then cut the turn off, and the message fell back to today's flow with only a phone number to go on. That produced most of the
"wrong" outcomes (decision accuracy 58% and 71%). Run v3 raised the limit for the evaluation
(`AGENT_PLANNING_TURN_TIMEOUT_S=240`, `AGENT_LLM_MAX_WAIT_S=120`); the evaluation report now counts the fallbacks and why.
Between the first and second run two small fixes were also made: asking for a phone number is labelled "verify identity" as in
today's flow, and an out-of-scope reply is a refusal.

## Table (off_ai against agent_on_v3, all 16 conversations, 24 messages)

| measure | target | today's flow + AI | agent on | holds? |
|---|---|---|---|---|
| wrong or unapproved actions | 0 | 0 | 0 | **yes** |
| unbacked facts reaching the customer | 0 | 0 | 0 | **yes** |
| solved without a person | higher | 87.5% | 87.5% | equal |
| needless handoffs | lower | 0 | 0 | equal |
| must-escalate cases escalated | 100% | 50% (1 of 2) | 50% (1 of 2) | **no (both)**: the same case is missed |
| reply quality (AI judge, 1-5) | higher | 4.11 | 3.91 | no (-0.21) |
| intent accuracy | | 93.8% | 100.0% | better |
| decision accuracy | | 87.5% | 83.3% | slightly lower (one message in 24) |
| agent gave up (fell back to today's flow) | lower | n/a | 2 of 24 (8%): 1 too slow, 1 two refused moves | |
| AI calls per message | lower | 2.62 | 5.33 | no |
| time per reply, median | lower | 2.6 s | 61 s | no (free-plan waiting) |
| time per reply, p95 | lower | 47 s | 239 s | no (free-plan waiting) |

Shadow run (4 conversations): the agent was asked on 3 turns and its first move agreed with today's flow on 3 (100%).

## Reading it

- **Safety holds in both modes:** nothing wrong was done, nothing unbacked was said.
- **The time is the free plan,** not the agent's thinking: the planner prompt is about 3,500 tokens and the plan allows
  8K tokens a minute, so about two calls a minute, and one message takes two to four calls. On a paid plan the same calls take
  a few seconds. The agent does use about twice as many AI calls per message.
- **What is still wrong in the sample (4 of 24 messages):** one message was cut off by the time limit even at 240 s (en-53);
  an Arabizi customer got an Arabic reply after a fallback (the usual flow cannot tell the style from a phone number alone); a
  return request asked "what is the reason?" before it said the refund rule (mixed-55); a customer who sent the same angry
  message twice was asked to describe the problem instead of being handed over (arabizi-49: the agent may choose a
  handover only after trying, and here it kept trying).
- The sample is 24 messages: one message is 4 points.

## Decision: agent mode stays OFF by default

Not every target holds (reply quality, calls and time are worse; escalation recall is equal but not 100%).
`AGENT_PLANNING_MODE` keeps its current default and `.env.example` is unchanged.

Smallest changes that would most likely fix it, to be re-measured:
1. A shorter planner prompt (send only the tools the message needs): fewer tokens, less waiting under a token limit.
2. Run on a plan whose token limit fits the prompt (or one model per job with its own limit) and set the turn limit to
   match the provider; the default 20 s is for a paid plan.
3. Keep the customer's language style when the loop gives up (carry the session's style into the fallback), and let a second
   identical very-angry message end in the handover the gold expects.
4. Then run the full 240 conversations, three times, before any switch.
