# E4 follow-up: diagnosis, fixes and extra tests (17 October 2026)

All agent-mode numbers are measured on the Groq free tier. Knowledge service: the real one (live mode) unless marked "stand-in".

## Headline agent-mode run, real knowledge service (team_b/reports/eval_presentation_agent_live_<style>_2026-10-17.*)
| style | conversations | intent | decision | citation | wrong actions | unbacked facts | fallbacks | reply time p50 / p95 |
|---|---|---|---|---|---|---|---|---|
| en | 60 | 95.0% | 80.3% | 68.0% | 0 | 0 | 0 | 7 s / 32 s |
| ar | 60 | 95.0% | 84.5% | 76.0% | 0 | 0 | 0 | 7 s / 31 s |
| mixed | 60 | 93.3% | 80.3% | 68.0% | 0 | 0 | 0 | 6 s / 31 s |
Arabizi (15 sampled) did not complete cleanly (17 of 21 turns fell back: rate limits); not reported.
The earlier run on the keyword stand-in is team_b/reports/eval_presentation_agent_<style>_2026-10-16.*: citation 68.0 / 68.0 / 68.0.

## What the diagnosis found
- The 8 "unbacked facts" of the first run were false alarms of the evaluation (greeting "...today" = النهاردة). Fixed.
- Two more evaluator false alarms found in the long run and fixed: references issued by the shop (CAN-30001) and passages the real knowledge service really returned. An invented reference, an unretrieved citation and another day ("tomorrow") are still flagged. Tests added.
- Citation 68% is not the stand-in search: the same 8 of 25 English policy turns miss against the real service. The agent understands the question (policy_question) but asks "tell me more" or hands over instead of searching.
- A real rules bug fixed: "when is customer support open" (and the Arabic / Arabizi versions) was read as a request for a person. Rules-only 240 run before/after: needless handoffs 8 -> 4, handoff precision 74.2% -> 85.2%, citation 39.0% -> 43.0%; missed escalations, wrong actions and NLU accuracy (87.7%) unchanged.
- Open: in agent mode a refund for an order outside the 14-day window sometimes loops on "what specific amount?" instead of refusing with the policy (T17 and long conversation L7). Not reproduced with a trace because the gpt-oss-120b daily token limit (200,000 per account) was used up on every key.

## Long conversations (24, 141 turns, real knowledge service, agent mode; diagnosis/longlive_<style>.jsonl)
- 8 fallback turns (6%), 0 unbacked facts, 0 order-data leaks after a failed identity check, 0 returns executed after a topic change.
- Writes really executed: cancel and voucher in all three languages, exchange in Arabic and mixed; the refund conversation never reached its confirmation (the agent asks for a reason first and the scripted "yes" answered that).
- 26 of 141 turns failed a check; most are the scripted conversation not matching the agent's own path, plus the refund-denied loop.
