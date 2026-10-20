# Conversation evaluation · llm · 2026-10-17

| style | conversations | turns | intent | decision | citation | locale | handoff precision | handoff recall | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| en | 60 | 71 | 95.0% | 80.3% | 68.0% | 100.0% | 66.7% | 85.7% | 7426 | 31581 |
| ar | 0 | 0 | — | — | — | — | — | — | — | — |
| mixed | 0 | 0 | — | — | — | — | — | — | — | — |
| arabizi | 0 | 0 | — | — | — | — | — | — | — | — |
| all | 60 | 71 | 95.0% | 80.3% | 68.0% | 100.0% | 66.7% | 85.7% | 7426 | 31581 |

## Conversations with every check right, by topic (all styles)

| topic | right | of |
|---|---|---|
| K1 | 6 | 6 |
| K2 | 3 | 4 |
| K3 | 3 | 3 |
| K4 | 2 | 3 |
| K5 | 0 | 3 |
| K6 | 1 | 2 |
| K7 | 1 | 2 |
| T1 | 3 | 3 |
| T10 | 1 | 1 |
| T11 | 0 | 1 |
| T12 | 1 | 1 |
| T13 | 1 | 1 |
| T14 | 0 | 1 |
| T15 | 1 | 1 |
| T16 | 1 | 1 |
| T17 | 0 | 1 |
| T18 | 1 | 1 |
| T19 | 1 | 1 |
| T2 | 3 | 3 |
| T20 | 0 | 1 |
| T21 | 1 | 1 |
| T22 | 1 | 1 |
| T3 | 3 | 3 |
| T4 | 3 | 3 |
| T5 | 3 | 3 |
| T6 | 2 | 2 |
| T7 | 3 | 3 |
| T8 | 1 | 3 |
| T9 | 1 | 1 |

## Reply quality, graded by the AI judge (advice only: never part of pass or fail)

| style | judged | failed | clarity | politeness | register | helpfulness |
|---|---|---|---|---|---|---|
| en | 71 | 0 | 3.82 | 4.77 | 4.49 | 3.58 |
| ar | 0 | 0 | — | — | — | — |
| mixed | 0 | 0 | — | — | — | — |
| arabizi | 0 | 0 | — | — | — | — |
| all | 71 | 0 | 3.82 | 4.77 | 4.49 | 3.58 |

## Measures for comparing runs (counted from this run; the gold only says what should happen)

| style | solved without a person | needless handoffs | missed escalations | wrong or unapproved actions | unbacked facts | AI calls per message | time p50 ms | time p95 ms |
|---|---|---|---|---|---|---|---|---|
| en | 50 of 53 (83.3% of all) | 3 | 1 of 7 | 0 of 0 writes | 0 | 4.76 | 7426 | 31581 |
| ar | 0 of 0 (— of all) | 0 | 0 of 0 | 0 of 0 writes | 0 | — | — | — |
| mixed | 0 of 0 (— of all) | 0 | 0 of 0 | 0 of 0 writes | 0 | — | — | — |
| arabizi | 0 of 0 (— of all) | 0 | 0 of 0 | 0 of 0 writes | 0 | — | — | — |
| all | 50 of 53 (83.3% of all) | 3 | 1 of 7 | 0 of 0 writes | 0 | 4.76 | 7426 | 31581 |

- solved without a person: conversations that ended with no handoff, out of those whose gold wants no person;
- needless handoff: a turn that handed over where the gold wants none; missed escalation: the gold wants a
  person and none was offered; wrong or unapproved action: a write the gold did not expect, or one sent
  without a rule check; unbacked fact: a sent reply with a number, date, id or link nothing the agent knew
  contains (it must be 0).

## The 10 conversations that went wrong most

### en-17 (en, K5): 3 wrong check(s)
- first wrong turn: #1: `the item I received is broken, what now`
- intent: expected policy_question, got complaint
- decision: expected answer, got clarify
- citation: expected one of return_policy@v2#s6, got none
- the agent said: I'm so sorry to hear the item arrived broken. Could you please provide your order number so I can look into this for you?

### en-19 (en, K5): 3 wrong check(s)
- first wrong turn: #1: `I got a defective item, who do I tell`
- intent: expected policy_question, got human_request
- decision: expected answer, got handoff
- citation: expected one of return_policy@v2#s6, got none
- the agent said: I'm sorry to hear about the defective item. I'm connecting you with a colleague from our customer care team now.

### en-21 (en, K6): 3 wrong check(s)
- first wrong turn: #1: `can you wrap my order as a gift`
- intent: expected policy_question, got none
- decision: expected answer, got clarify
- citation: expected one of faq@v1#q05, got none
- the agent said: I would be happy to help with gift wrapping for you. Could you please provide your order number so I can look into it?

### en-49 (en, T11): 3 wrong check(s)
- first wrong turn: #1: `This is ridiculous!!! Worst service ever, I am so angry, stop asking me questions!!!`
- decision: expected clarify, got answer
- the agent said: I'm sorry about the trouble, I'll do my best to sort this out for you.

I am so sorry to hear you are feeling this way, and I completely understand your frustra

### en-09 (en, K2): 2 wrong check(s)
- first wrong turn: #1: `how much do you charge for delivery`
- decision: expected answer, got clarify
- citation: expected one of shipping_policy@v1#s3, shipping_policy@v1#s2, got none
- the agent said: I'd be happy to help with that. Could you tell me a little more about what you need help with?

### en-14 (en, K4): 2 wrong check(s)
- first wrong turn: #1: `when will my refund reach my card?`
- decision: expected answer, got clarify
- citation: expected one of refund_policy@v1#s2, got none
- the agent said: I'd be happy to help check on that for you. Could you please provide your order number so I can look into it?

### en-18 (en, K5): 2 wrong check(s)
- first wrong turn: #1: `my product arrived damaged what should I do`
- decision: expected answer, got clarify
- citation: expected one of return_policy@v2#s6, got none
- the agent said: I'm so sorry to hear your product arrived damaged. To help you with this, could you please provide your order number?

### en-23 (en, K7): 2 wrong check(s)
- first wrong turn: #1: `when is customer support open`
- decision: expected answer, got handoff
- citation: expected one of faq@v1#q01, got none
- the agent said: I'm connecting you with a colleague from our customer care team now.

### en-55 (en, T17): 2 wrong check(s)
- first wrong turn: #2: `01012345601`
- decision: expected refuse, got clarify
- citation: expected one of refund_policy@v1#s1, got none
- the agent said: Thanks for confirming your details. To make sure I process the right request, what specific amount are you asking for?

### en-58 (en, T20): 2 wrong check(s)
- first wrong turn: #2: `01012345601`
- decision: expected confirm, got clarify
- text: expected it to contain NS-20745
- the agent said: Thanks for that. To help with your request, what amount are you asking for?

