# Conversation evaluation · llm · 2026-10-16

| style | conversations | turns | intent | decision | citation | locale | handoff precision | handoff recall | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| en | 0 | 0 | — | — | — | — | — | — | — | — |
| ar | 0 | 0 | — | — | — | — | — | — | — | — |
| mixed | 60 | 71 | 93.3% | 80.3% | 68.0% | 98.6% | 62.5% | 71.4% | 14216 | 59699 |
| arabizi | 0 | 0 | — | — | — | — | — | — | — | — |
| all | 60 | 71 | 93.3% | 80.3% | 68.0% | 98.6% | 62.5% | 71.4% | 14216 | 59699 |

## Conversations with every check right, by topic (all styles)

| topic | right | of |
|---|---|---|
| K1 | 5 | 6 |
| K2 | 2 | 4 |
| K3 | 3 | 3 |
| K4 | 3 | 3 |
| K5 | 0 | 3 |
| K6 | 2 | 2 |
| K7 | 1 | 2 |
| T1 | 3 | 3 |
| T10 | 1 | 1 |
| T11 | 0 | 1 |
| T12 | 1 | 1 |
| T13 | 1 | 1 |
| T14 | 0 | 1 |
| T15 | 1 | 1 |
| T16 | 0 | 1 |
| T17 | 0 | 1 |
| T18 | 0 | 1 |
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
| T9 | 0 | 1 |

## Reply quality, graded by the AI judge (advice only: never part of pass or fail)

| style | judged | failed | clarity | politeness | register | helpfulness |
|---|---|---|---|---|---|---|
| en | 0 | 0 | — | — | — | — |
| ar | 0 | 0 | — | — | — | — |
| mixed | 71 | 0 | 4.00 | 4.96 | 4.00 | 3.58 |
| arabizi | 0 | 0 | — | — | — | — |
| all | 71 | 0 | 4.00 | 4.96 | 4.00 | 3.58 |

## Measures for comparing runs (counted from this run; the gold only says what should happen)

| style | solved without a person | needless handoffs | missed escalations | wrong or unapproved actions | unbacked facts | AI calls per message | time p50 ms | time p95 ms |
|---|---|---|---|---|---|---|---|---|
| en | 0 of 0 (— of all) | 0 | 0 of 0 | 0 of 0 writes | 0 | — | — | — |
| ar | 0 of 0 (— of all) | 0 | 0 of 0 | 0 of 0 writes | 0 | — | — | — |
| mixed | 50 of 53 (83.3% of all) | 3 | 2 of 7 | 0 of 0 writes | 4 | 4.82 | 14216 | 59699 |
| arabizi | 0 of 0 (— of all) | 0 | 0 of 0 | 0 of 0 writes | 0 | — | — | — |
| all | 50 of 53 (83.3% of all) | 3 | 2 of 7 | 0 of 0 writes | 4 | 4.82 | 14216 | 59699 |

- solved without a person: conversations that ended with no handoff, out of those whose gold wants no person;
- needless handoff: a turn that handed over where the gold wants none; missed escalation: the gold wants a
  person and none was offered; wrong or unapproved action: a write the gold did not expect, or one sent
  without a rule check; unbacked fact: a sent reply with a number, date, id or link nothing the agent knew
  contains (it must be 0).

## The 10 conversations that went wrong most

### mixed-17 (mixed, K5): 3 wrong check(s)
- first wrong turn: #1: `الـ product وصلني مكسور اعمل ايه؟`
- intent: expected policy_question, got complaint
- decision: expected answer, got clarify
- citation: expected one of return_policy@v2#s6, got none
- the agent said: أنا آسف جدًا إن المنتج وصلك مكسور، ده مش اللي مستنيينه منك. عشان أقدر أساعدك في أسرع وقت، ممكن تديني رقم الأوردر؟

### mixed-18 (mixed, K5): 3 wrong check(s)
- first wrong turn: #1: `استلمت item damaged اعمل ايه؟`
- intent: expected policy_question, got return_request
- decision: expected answer, got clarify
- citation: expected one of return_policy@v2#s6, got none
- the agent said: أنا آسف جدًا إنك استلمت المنتج متضرر. عشان أقدر أساعدك في الموضوع ده، ممكن تديني رقم الأوردر؟

### mixed-19 (mixed, K5): 3 wrong check(s)
- first wrong turn: #1: `الحاجة وصلت defective مين اكلمه؟`
- intent: expected policy_question, got human_request
- decision: expected answer, got handoff
- citation: expected one of return_policy@v2#s6, got none
- the agent said: أنا آسف جدًا إن الحاجة وصلتك كده. هحولك دلوقتي لزميل من فريق خدمة العملاء عشان يتابع معاك الموضوع.

### mixed-06 (mixed, K1): 2 wrong check(s)
- first wrong turn: #1: `ينفع ارجع order من اسبوعين؟`
- decision: expected answer, got clarify
- citation: expected one of return_policy@v2#s2, got none
- the agent said: أكيد، أقدر أساعدك في الموضوع ده. عشان أقدر أعملك اللي محتاجه، ممكن تبعتلي رقم الأوردر؟

### mixed-09 (mixed, K2): 2 wrong check(s)
- first wrong turn: #1: `بتاخدوا كام على الـ delivery؟`
- decision: expected answer, got clarify
- citation: expected one of shipping_policy@v1#s3, shipping_policy@v1#s2, got none
- the agent said: أهلاً بيك، ممكن توضحلي أكتر محتاج مساعدة في إيه؟

### mixed-10 (mixed, K2): 2 wrong check(s)
- first wrong turn: #1: `الـ delivery fee كام؟`
- decision: expected answer, got clarify
- citation: expected one of shipping_policy@v1#s3, shipping_policy@v1#s2, got none
- the agent said: أهلاً بيك، ممكن توضحلي أكتر محتاج مساعدة في إيه؟

### mixed-22 (mixed, K7): 2 wrong check(s)
- first wrong turn: #1: `الـ customer service بتشتغل امتى؟`
- decision: expected answer, got handoff
- citation: expected one of faq@v1#q01, got none
- the agent said: أكيد، هحولك دلوقتي لزميل من فريق خدمة العملاء.

### mixed-47 (mixed, T9): 2 wrong check(s)
- first wrong turn: #2: `اقصد الـ warranty للأجهزة`
- decision: expected handoff, got clarify
- escalation: expected no_evidence, got None
- the agent said: أهلاً بيك، عشان أقدر أساعدك صح، ممكن توضحلي أكتر محتاج مساعدة في إيه بالظبط؟

### mixed-49 (mixed, T11): 2 wrong check(s)
- first wrong turn: #2: `الـ service ده worst حاجة، مش معقول!!!`
- decision: expected handoff, got clarify
- escalation: expected high_frustration, got None
- the agent said: أنا فاهم إنك زعلان من التجربة دي، وده مش اللي بنحبه نسمع. عشان أقدر أساعدك وأحل المشكلة، ممكن تقولي رقم الأوردر؟

### mixed-55 (mixed, T17): 2 wrong check(s)
- first wrong turn: #2: `01012345601`
- decision: expected refuse, got clarify
- citation: expected one of refund_policy@v1#s1, got none
- the agent said: تمام، خلصنا التأكيد. عشان أكمل معاك، إيه المبلغ المطلوب للـ refund؟

