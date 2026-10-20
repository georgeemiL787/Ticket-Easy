# Conversation evaluation · llm · 2026-10-17

| style | conversations | turns | intent | decision | citation | locale | handoff precision | handoff recall | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| en | 0 | 0 | — | — | — | — | — | — | — | — |
| ar | 0 | 0 | — | — | — | — | — | — | — | — |
| mixed | 0 | 0 | — | — | — | — | — | — | — | — |
| arabizi | 15 | 21 | 73.3% | 76.2% | 40.0% | 100.0% | 33.3% | 100.0% | 19600 | 41313 |
| all | 15 | 21 | 73.3% | 76.2% | 40.0% | 100.0% | 33.3% | 100.0% | 19600 | 41313 |

## Conversations with every check right, by topic (all styles)

| topic | right | of |
|---|---|---|
| K2 | 0 | 1 |
| K4 | 1 | 1 |
| K5 | 0 | 1 |
| K7 | 0 | 1 |
| T10 | 1 | 1 |
| T12 | 1 | 1 |
| T14 | 0 | 1 |
| T16 | 1 | 1 |
| T18 | 1 | 1 |
| T2 | 1 | 1 |
| T20 | 0 | 1 |
| T22 | 1 | 1 |
| T4 | 1 | 1 |
| T6 | 1 | 1 |
| T8 | 0 | 1 |

## Reply quality, graded by the AI judge (advice only: never part of pass or fail)

| style | judged | failed | clarity | politeness | register | helpfulness |
|---|---|---|---|---|---|---|
| en | 0 | 0 | — | — | — | — |
| ar | 0 | 0 | — | — | — | — |
| mixed | 0 | 0 | — | — | — | — |
| arabizi | 21 | 0 | 3.24 | 4.62 | 4.00 | 3.14 |
| all | 21 | 0 | 3.24 | 4.62 | 4.00 | 3.14 |

## Measures for comparing runs (counted from this run; the gold only says what should happen)

| style | solved without a person | needless handoffs | missed escalations | wrong or unapproved actions | unbacked facts | AI calls per message | time p50 ms | time p95 ms |
|---|---|---|---|---|---|---|---|---|
| en | 0 of 0 (— of all) | 0 | 0 of 0 | 0 of 0 writes | 0 | — | — | — |
| ar | 0 of 0 (— of all) | 0 | 0 of 0 | 0 of 0 writes | 0 | — | — | — |
| mixed | 0 of 0 (— of all) | 0 | 0 of 0 | 0 of 0 writes | 0 | — | — | — |
| arabizi | 12 of 14 (80.0% of all) | 2 | 0 of 1 | 0 of 0 writes | 0 | 4.24 | 19600 | 41313 |
| all | 12 of 14 (80.0% of all) | 2 | 0 of 1 | 0 of 0 writes | 0 | 4.24 | 19600 | 41313 |

- solved without a person: conversations that ended with no handoff, out of those whose gold wants no person;
- needless handoff: a turn that handed over where the gold wants none; missed escalation: the gold wants a
  person and none was offered; wrong or unapproved action: a write the gold did not expect, or one sent
  without a rule check; unbacked fact: a sent reply with a number, date, id or link nothing the agent knew
  contains (it must be 0).

## The 6 conversations that went wrong most

### arabizi-17 (arabizi, K5): 3 wrong check(s)
- first wrong turn: #1: `el montag weselny maksour a3mel eh?`
- intent: expected policy_question, got complaint
- decision: expected answer, got clarify
- citation: expected one of return_policy@v2#s6, got none
- the agent said: Tamam, a2dar asa3dak fe da. eh ra2am el order?

### arabizi-07 (arabizi, K2): 2 wrong check(s)
- first wrong turn: #1: `el shahn bekam?`
- intent: expected policy_question, got greeting
- citation: expected one of shipping_policy@v1#s3, shipping_policy@v1#s2, got none
- the agent said: Ana b2afak shakran, el shahn beek? Ahlan beek, a2dar asa3dak ezay?

### arabizi-22 (arabizi, K7): 2 wrong check(s)
- first wrong turn: #1: `eh mawa3eed khedmet el 3omala2?`
- decision: expected answer, got handoff
- citation: expected one of faq@v1#q01, got none
- the agent said: Akeed. Ha7awelak delwa2ty le zameel men fari2 khedmet el 3omala2.

### arabizi-44 (arabizi, T8): 2 wrong check(s)
- first wrong turn: #1: `el gaw eh el naharda?`
- intent: expected none, got greeting
- decision: expected clarify/refuse, got answer
- the agent said: Ana kheyra, shukran 3ala es2ol! Ahlan beek, a2dar asa3dak ezay el naharda?

### arabizi-52 (arabizi, T14): 2 wrong check(s)
- first wrong turn: #1: `momken tersha77ly mat3am kewayes?`
- intent: expected none, got policy_question
- decision: expected clarify/refuse, got answer
- the agent said: Da elly betqolo siyastena:

“يمكن استبدال المنتج بمقاس أو لون آخر خلال 14 يومًا من الاستلام بنفس شروط الاسترجاع، وبشرط توفر المقاس أو اللون المطلوب. إذا كان سعر

### arabizi-58 (arabizi, T20): 2 wrong check(s)
- first wrong turn: #2: `01012345601`
- decision: expected confirm, got handoff
- text: expected it to contain NS-20745
- the agent said: Shokran 3ala el ra2am. El talab da me7tag mwafa2a men fari2 khedmet el 3omala2, fa 7awaltoh le zameel yeraga3o.

