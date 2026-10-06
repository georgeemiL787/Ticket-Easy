# Metrics

What the dashboard counts, exactly. Every number is **per business (tenant) and per time bucket** (`hour`, `day`, `week`
starting Monday, or `month`, all in UTC), optionally narrowed by a **filter** (language, intent, decision, escalation
reason). Code: `brain/metrics.py` (`MetricsService`, one function per metric, signature
`(tenant_id, start, end, bucket="day", filters=None)`). Data: the summary rows below, never message text.

Conventions

- A window is `[start, end)`. One point is returned for every bucket from the one holding `start`, empty buckets included
  (zero counts, `rate = null`, `p50/p95 = null`).
- A **conversation** is counted in the bucket of its **first turn inside the window**. Its later turns still count for
  the turn-level metrics (latency, language mix, ...) in their own buckets.
- A conversation **has a case** when any of its turns in the window has `decision = handoff`.
- A **rate** is returned as `numerator`, `denominator` and `rate` (`null` when the denominator is 0).
- **p50 / p95** use the nearest-rank method: the value at position `ceil(p × n)` of the sorted values.
- Filters apply to turns; tool calls and policy answers are restricted to the turns that pass. Case metrics (7 and 8
  below) use the case's `package.reason`, `language` and `intents` for the same filters.

## The metrics

| # | Metric (function) | Definition |
|---|---|---|
| 1 | Conversations (`conversations`) | Number of distinct conversations. |
| 2 | Automation rate (`automation_rate`) | Conversations that ended **without a case** ÷ conversations. |
| 3 | Resolved with an action (`resolved_with_action`) | Conversations with **no case** and at least one **successful write** tool call (`operation_kind` is not `read`, `status = success`) ÷ conversations. |
| 4 | Escalation rate by reason (`escalation_by_reason`) | For every reason: conversations whose **first** handoff had that reason ÷ conversations. Returns the counts and the rates. |
| 5 | Time to first human reply (`time_to_first_human_reply`) | Seconds from the case being opened to its first `reply` event; p50, p95 and the number of cases that got a reply. Cases without a reply are left out. A case counts in the bucket where it was opened. |
| 6 | Case resolution time (`case_resolution_time`) | Seconds from the case being opened to its `resolved` event; p50, p95, count. Open, claimed and returned cases are left out. |
| 7 | Action failure rate by tool and error code (`action_failures`) | Over tool calls that are **not reads**: per tool, `calls`, `failures` (status `error`), `rate = failures ÷ calls` and the failures counted by `error_code`. |
| 8 | Unverified results (`unverified_results`) | Number of turns that ended in a handoff with reason `unverified_result` (a write whose outcome could not be confirmed). |
| 9 | Unanswered rate (`unanswered_rate`) | Turns where the policy search found nothing ÷ turns where a policy search ran (it found passages or found nothing). |
| 10 | Policy outcomes by rule (`policy_outcomes`) | Rule checker answers counted by `"<action>:<reason_code>:<decision>"` (the rule is named by its reason code). |
| 11 | Dependency errors by service (`dependency_errors`) | Errors recorded in turns, counted by the service they are about: `policy_search`, `safety_screen`, `rule_checker`, `shop_tools`, else `other`. A turn with two failed search attempts counts 2. |
| 12 | Latency p50 / p95 (`latency`) | Milliseconds of `latency_ms` per turn, overall, and for every stage that ran (skipped stages are not timed). |
| 13 | Language mix (`language_mix`) | Turns counted by language (`en`, `ar`, `mixed`, `arabizi`, `unknown`). |
| 14 | AI fallback rate (`ai_fallback_rate`) | Turns whose understanding fell back to the rules (`rules_fallback`) ÷ turns where the AI model was asked (`llm` or `rules_fallback`). 0 of 0 when no AI model is configured. |

## Summary rows (migration 002)

Written in the same transaction as the trace, by `facts_from_trace` (`domain/facts.py`). The in-memory store keeps the same
rows in lists. They hold no message text or personal value and **outlive the trace**: deleting old traces (retention)
does not change the numbers of past periods.

- `turn_facts(trace_id, tenant_id, conversation_id, created_at, decision, escalation_reason, language, intent, latency_ms,
  evidence_empty, nlu_method, tool_errors, dependency_errors)` plus `evidence_count` (passages used) and `stage_ms` (stage
  → ms). One row per customer turn. `dependency_errors` is the list of services. `intent` is the active intent.
- `tool_call_facts(trace_id, tenant_id, created_at, tool, operation_kind, status, error_code, latency_ms)`. One row per tool
  call of any trace (a human's approval also executes tools).
- `policy_facts(trace_id, tenant_id, created_at, action, decision, reason_code)`. One row per rule checker answer.

`created_at` is the moment the store saved the trace.

## Assumptions to review

- "Rule" in metric 10 is the reason code; a finer per-rule id needs the rule checker to put the rule id on the trace.
- Dependency errors are classified by the first words of the error text recorded on the trace; new services must follow the
  same convention or they land in `other`.
- Time to first reply and resolution time are measured from the moment the case was opened, not from the customer's last
  message, and ignore business hours.
- A message the customer sends while a human owns the chat is a turn with `decision = handoff`; it does not open a second
  case, so it does not change the number of conversations that had a case.
