# Conversation evaluation · rules · 2026-10-17

| style | conversations | turns | intent | decision | citation | locale | handoff precision | handoff recall | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| en | 60 | 71 | 76.7% | 78.9% | 52.0% | 100.0% | 85.7% | 85.7% | 259 | 681 |
| ar | 60 | 71 | 66.7% | 73.2% | 32.0% | 100.0% | 75.0% | 85.7% | 270 | 881 |
| mixed | 60 | 71 | 66.7% | 73.2% | 48.0% | 98.6% | 85.7% | 85.7% | 232 | 809 |
| arabizi | 60 | 71 | 40.0% | 45.1% | 24.0% | 97.2% | 55.6% | 71.4% | 303 | 1039 |
| all | 240 | 284 | 62.5% | 67.6% | 39.0% | 98.9% | 74.2% | 82.1% | 267 | 881 |

## Conversations with every check right, by topic (all styles)

| topic | right | of |
|---|---|---|
| K1 | 13 | 24 |
| K2 | 6 | 16 |
| K3 | 7 | 12 |
| K4 | 3 | 12 |
| K5 | 0 | 12 |
| K6 | 3 | 8 |
| K7 | 1 | 8 |
| T1 | 12 | 12 |
| T10 | 2 | 4 |
| T11 | 4 | 4 |
| T12 | 4 | 4 |
| T13 | 4 | 4 |
| T14 | 4 | 4 |
| T15 | 2 | 4 |
| T16 | 3 | 4 |
| T17 | 3 | 4 |
| T18 | 2 | 4 |
| T19 | 2 | 4 |
| T2 | 5 | 12 |
| T20 | 3 | 4 |
| T21 | 2 | 4 |
| T22 | 3 | 4 |
| T3 | 6 | 12 |
| T4 | 7 | 12 |
| T5 | 10 | 12 |
| T6 | 8 | 8 |
| T7 | 11 | 12 |
| T8 | 11 | 12 |
| T9 | 0 | 4 |

## Measures for comparing runs (counted from this run; the gold only says what should happen)

| style | solved without a person | needless handoffs | missed escalations | wrong or unapproved actions | unbacked facts | AI calls per message | time p50 ms | time p95 ms |
|---|---|---|---|---|---|---|---|---|
| en | 52 of 53 (86.7% of all) | 1 | 1 of 7 | 0 of 0 writes | 0 | 0.00 | 259 | 681 |
| ar | 51 of 53 (85.0% of all) | 2 | 1 of 7 | 0 of 0 writes | 0 | 0.00 | 270 | 881 |
| mixed | 52 of 53 (86.7% of all) | 1 | 1 of 7 | 0 of 0 writes | 1 | 0.00 | 232 | 809 |
| arabizi | 50 of 53 (83.3% of all) | 4 | 2 of 7 | 0 of 0 writes | 0 | 0.00 | 303 | 1039 |
| all | 205 of 212 (85.4% of all) | 8 | 5 of 28 | 0 of 0 writes | 1 | 0.00 | 267 | 881 |

- solved without a person: conversations that ended with no handoff, out of those whose gold wants no person;
- needless handoff: a turn that handed over where the gold wants none; missed escalation: the gold wants a
  person and none was offered; wrong or unapproved action: a write the gold did not expect, or one sent
  without a rule check; unbacked fact: a sent reply with a number, date, id or link nothing the agent knew
  contains (it must be 0).

## The 10 conversations that went wrong most

### arabizi-56 (arabizi, T18): 7 wrong check(s)
- first wrong turn: #1: `el order NS-20877 wasal fein?`
- intent: expected order_status, got none
- decision: expected verify_identity, got clarify
- the agent said: Momken twadda7ly aktar me7tag mosa3da fe eh?

### arabizi-20 (arabizi, K6): 4 wrong check(s)
- first wrong turn: #1: `fi taghleef hedeya?`
- intent: expected policy_question, got none
- decision: expected answer, got clarify
- citation: expected one of faq@v1#q05, got none
- locale: expected arabizi, got en
- the agent said: Could you tell me a little more about what you need help with?

### arabizi-48 (arabizi, T10): 4 wrong check(s)
- first wrong turn: #1: `el order NS-20877 wasal fein?`
- intent: expected order_status, got none
- decision: expected verify_identity, got clarify
- the agent said: Momken twadda7ly aktar me7tag mosa3da fe eh?

### arabizi-55 (arabizi, T17): 4 wrong check(s)
- first wrong turn: #1: `3ayez floosy bta3et NS-20512`
- intent: expected refund_request, got none
- decision: expected verify_identity, got clarify
- the agent said: Momken twadda7ly aktar me7tag mosa3da fe eh?

### mixed-48 (mixed, T10): 4 wrong check(s)
- first wrong turn: #1: `where is order NS-20877 بتاعي`
- intent: expected order_status, got none
- decision: expected verify_identity, got clarify
- the agent said: ممكن توضحلي أكتر محتاج مساعدة في إيه؟

### ar-03 (ar, K1): 3 wrong check(s)
- first wrong turn: #1: `اقدر ارجع الحاجة بعد ١٠ ايام؟`
- intent: expected policy_question, got return_request
- decision: expected answer, got clarify
- citation: expected one of return_policy@v2#s2, got none
- the agent said: تمام، أقدر أساعدك في ده. إيه رقم الأوردر؟

### ar-05 (ar, K1): 3 wrong check(s)
- first wrong turn: #1: `هل ينفع ارجع طلب اشتريته من اسبوعين؟`
- intent: expected policy_question, got return_request
- decision: expected answer, got clarify
- citation: expected one of return_policy@v2#s2, got none
- the agent said: تمام، أقدر أساعدك في ده. إيه رقم الأوردر؟

### ar-08 (ar, K2): 3 wrong check(s)
- first wrong turn: #1: `مصاريف التوصيل كام؟`
- intent: expected policy_question, got none
- decision: expected answer, got clarify
- citation: expected one of shipping_policy@v1#s3, shipping_policy@v1#s2, got none
- the agent said: ممكن توضحلي أكتر محتاج مساعدة في إيه؟

### ar-09 (ar, K2): 3 wrong check(s)
- first wrong turn: #1: `بتاخدوا كام على الشحن؟`
- intent: expected policy_question, got none
- decision: expected answer, got clarify
- citation: expected one of shipping_policy@v1#s3, shipping_policy@v1#s2, got none
- the agent said: ممكن توضحلي أكتر محتاج مساعدة في إيه؟

### ar-10 (ar, K2): 3 wrong check(s)
- first wrong turn: #1: `كام رسوم التوصيل؟`
- intent: expected policy_question, got none
- decision: expected answer, got clarify
- citation: expected one of shipping_policy@v1#s3, shipping_policy@v1#s2, got none
- the agent said: ممكن توضحلي أكتر محتاج مساعدة في إيه؟

