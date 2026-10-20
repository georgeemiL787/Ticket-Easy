# Owner review and build: audit, changes and measurements (2026-10-11)

## Current flow (before)

Specification → tool request (triage, multi-slice retrieval) → generation → **review** → approve → connector → build → sandbox tests → sandbox publication.

Review on `/proposals/{id}` had two parallel ways to decide the same thing:

1. The owner typed prose for each AI question **and** for each server-derived access requirement, pressed *Save answers*, then *Check answers* (LLM reconciliation).
2. The owner then filled the structured access policy (principals, scope, five ownership-check fields, effects), pressed *Accept*. This confirmed every access requirement again and reset the version to `needs_reconciliation`.
3. *Check answers* again (a second LLM call over the same answers), then *Approve*.

On the database from the last E2E run, every approved proposal had 2 reconciliations, and 6 answer rows for 2 questions + 2 requirements.

## Root causes

| Pain point | Root cause |
|---|---|
| Too many answers | Access requirements were asked as prose **and** decided in the policy; the prose was then superseded by the policy. |
| Second LLM check | `accept_policy` always reset the version to `needs_reconciliation`, although the question answers had not changed. |
| Confusing page | The policy form appeared before the questions, while the "Next" banner said to answer every requirement in prose. Readiness, quality tables, traceability and raw bindings were mixed into the decision path (33 inputs, ~5,700 px). |
| Empty fields | No suggestions: the ownership check needed a step, status, JSON pointer and identity field, which the response schema already implies. |
| `/proposals` 404, `GET /actions/build_artifact` 405 | There was no proposals index; action URLs opened in a browser returned raw JSON. |
| Build friction | The connector had to be saved, the page reloaded, then Build pressed. Build errors linked nowhere. |
| Triage misses | The operation index listed only the first 12 response fields alphabetically: every order operation (22 fields) lost `items`, `status` and totals, so "show an order's items" was judged unavailable. |
| Triage/generation tokens | One 12K-char system prompt carried every task's instructions, including reconciliation text that no call using it needed. It shrank the triage index to ~18 operations per call. |

## Changes

- **Policy decides access once.** Prose boxes for requirements are gone from the page (the API still accepts them). Accepting the policy settles the review **without a model call** when the current answers were already checked on this version, nothing they cite changed, and the earlier check passed the same rule the approval gate applies (`Review.reusable_assessment`). The reused check is a new reconciliation record with `reused_from` and a `reused_assessment` diagnostic (`model_calls: 0`).
- **Fewer things sent to the judge.** Unanswered requirements are recorded as undecided without being sent. The model sees only the answers it assesses, as id/question/text, not the policy JSON stored as requirement answers.
- **Suggestions, never decisions.**
  - The ownership check is prefilled only when a read step's success response *requires* an identity-like field. The evidence is shown.
  - Access questions are prefilled from the owner's accepted policy.
  - Configuration questions are prefilled with a default the API declares.
  - All of these are form defaults. Nothing is saved or confirmed until the owner submits, and submitted text is judged like any other answer.
- **Isolated answer assistant** (`answer_suggestion`): own prompt and run, retrieval done in code (the proposal's operations plus operations ranked by the questions). It never sees findings. It must mark business decisions as owner decisions with neutral options (any drafted text there is discarded). It is cached by input hash.
- **Page:** checklist of what blocks approval → summary → *Who can use this tool* → *Questions* (**Save and check answers** in one step) → decision → build. Technical detail is collapsed. A first-pass quality rating is shown as context until a check re-rates it.
- **Build:** *Save connector and build artifact* in one step. Build errors carry a `recovery_url`. `GET /actions/*` redirects to the matching page. A new `/proposals` overview groups proposals by next step.
- **Prompts per task:** `system_prompt(kind)` assembles the shared rules plus the task's section (triage 2.3K chars, generation 7.5K, was 12.1K for all).
- **Retrieval:** index entries list up to 32 response fields. Triage starts from `TOOLS_TRIAGE_INDEX_CHARS` (8,000, never above `TOOLS_CAPABILITY_INDEX_CHARS`).

Unchanged: approval still requires a successful current reconciliation, confirmed requirements and a passing quality gate (`decide`); policies, artifacts and approvals stay hash-bound; sandbox publication stays separate from production (`Runtime-ready: no`).

## Measurements

Provider-reported tokens (Groq `openai/gpt-oss-120b`, fallback disabled unless noted). Estimates are labelled.

**Triage, per complete workflow** (expansion rounds included, AstroCommerce spec, 3 goals × 4 trials):

| | Correct | LLM calls | Input tokens | Output tokens | Input per correct |
|---|---|---|---|---|---|
| Original | 10/12 | 22 | 115,302 | 4,448 | 11,530 |
| New | **12/12** | 20 | **81,916** | 3,687 | **6,826** |

An intermediate variant (prompt split only, 48-operation slices) scored 5/12 at 7,464 input tokens per call. That variant was rejected; the 12-field cut-off was found to be the cause of the misses.

**One review workflow, live** (fresh order-lookup request, new flow, DB copy): triage 4,051/207, generation 4,065/929, one reconciliation 4,407/558 = **14,217 tokens, 3 model calls**, approved at version 1. The owner typed nothing: one principal tick, the prefilled check, the prefilled answers. For comparison, averages from the E2E database for the old flow: triage 5,339/194, generation 4,844/1,079, two reconciliations 5,042/703 each ≈ **22,350 tokens, 4 model calls**. These are different runs, so the comparison is indicative.

**Reconciliation input after policy acceptance** (when it still runs; estimate from real E2E proposals): 10.6K → 7.2K data chars (≈5.9K → 4.5K tokens).

**Owner effort on the E2E-shaped proposal:**

| | Before | After |
|---|---|---|
| Typed answers | 4 | 0–2 |
| Policy fields | ~11 | 1 |
| Clicks to build | 7 | 4 |
| Reconciliation calls | 2 | 1 |

Visible inputs on an open proposal: 19 → 12; page height 5,669 → 4,620 px.

**Tests:** 613 passed (baseline 595). Changed assertions:
- Two prompt tests now check the reconciliation prompt, the one actually sent.
- One budget test checks that the budget counts the whole system prompt, rather than a fixed size.
- The policy lifecycle test now expects settlement without a model call.
- One blank-requirement test allows the substitute's revision path.

## Limitations and next steps

1. Sandbox execution and publication were not re-run live: the AstroCommerce target (:8080) was down, and tests need its test accounts.
2. The answer assistant is unit-tested with a substitute; its live draft quality is not yet evaluated.
3. Goal ranking is lexical ("delivery" does not find "shipping"), so such requests still need extra retrieval rounds. A synonym/tag pass or embedding ranking would cut them.
4. A reconciliation that creates a material revision still forces a second check on the new version. Copying findings for unchanged questions across versions would remove it.
5. The evaluation is small (3 goals, 1 API). Extend it to the NileStay and service-desk fixtures before tuning `TOOLS_TRIAGE_INDEX_CHARS` further.
