# Conversation evaluation · llm · 2026-10-17

| style | conversations | turns | intent | decision | citation | locale | handoff precision | handoff recall | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| en | 0 | 0 | — | — | — | — | — | — | — | — |
| ar | 60 | 71 | 95.0% | 84.5% | 76.0% | 100.0% | 60.0% | 85.7% | 7174 | 31427 |
| mixed | 0 | 0 | — | — | — | — | — | — | — | — |
| arabizi | 0 | 0 | — | — | — | — | — | — | — | — |
| all | 60 | 71 | 95.0% | 84.5% | 76.0% | 100.0% | 60.0% | 85.7% | 7174 | 31427 |

## Conversations with every check right, by topic (all styles)

| topic | right | of |
|---|---|---|
| K1 | 6 | 6 |
| K2 | 4 | 4 |
| K3 | 3 | 3 |
| K4 | 3 | 3 |
| K5 | 0 | 3 |
| K6 | 2 | 2 |
| K7 | 0 | 2 |
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
| T20 | 1 | 1 |
| T21 | 1 | 1 |
| T22 | 1 | 1 |
| T3 | 3 | 3 |
| T4 | 3 | 3 |
| T5 | 3 | 3 |
| T6 | 2 | 2 |
| T7 | 3 | 3 |
| T8 | 0 | 3 |
| T9 | 1 | 1 |

## Reply quality, graded by the AI judge (advice only: never part of pass or fail)

| style | judged | failed | clarity | politeness | register | helpfulness |
|---|---|---|---|---|---|---|
| en | 0 | 0 | — | — | — | — |
| ar | 71 | 0 | 3.99 | 4.94 | 4.17 | 3.68 |
| mixed | 0 | 0 | — | — | — | — |
| arabizi | 0 | 0 | — | — | — | — |
| all | 71 | 0 | 3.99 | 4.94 | 4.17 | 3.68 |

## Measures for comparing runs (counted from this run; the gold only says what should happen)

| style | solved without a person | needless handoffs | missed escalations | wrong or unapproved actions | unbacked facts | AI calls per message | time p50 ms | time p95 ms |
|---|---|---|---|---|---|---|---|---|
| en | 0 of 0 (— of all) | 0 | 0 of 0 | 0 of 0 writes | 0 | — | — | — |
| ar | 49 of 53 (81.7% of all) | 4 | 1 of 7 | 0 of 0 writes | 0 | 4.96 | 7174 | 31427 |
| mixed | 0 of 0 (— of all) | 0 | 0 of 0 | 0 of 0 writes | 0 | — | — | — |
| arabizi | 0 of 0 (— of all) | 0 | 0 of 0 | 0 of 0 writes | 0 | — | — | — |
| all | 49 of 53 (81.7% of all) | 4 | 1 of 7 | 0 of 0 writes | 0 | 4.96 | 7174 | 31427 |

- solved without a person: conversations that ended with no handoff, out of those whose gold wants no person;
- needless handoff: a turn that handed over where the gold wants none; missed escalation: the gold wants a
  person and none was offered; wrong or unapproved action: a write the gold did not expect, or one sent
  without a rule check; unbacked fact: a sent reply with a number, date, id or link nothing the agent knew
  contains (it must be 0).

## The 10 conversations that went wrong most

### ar-17 (ar, K5): 3 wrong check(s)
- first wrong turn: #1: `المنتج وصلني مكسور اعمل ايه؟`
- intent: expected policy_question, got return_request
- decision: expected answer, got clarify
- citation: expected one of return_policy@v2#s6, got none
- the agent said: أنا آسف جدًا إن المنتج وصلك مكسور، ده مش اللي مستنيينه منك. عشان أقدر أساعدك في أسرع وقت، ممكن تديني رقم الأوردر؟

### ar-18 (ar, K5): 3 wrong check(s)
- first wrong turn: #1: `استلمت حاجة بايظة اعمل ايه؟`
- intent: expected policy_question, got return_request
- decision: expected answer, got clarify
- citation: expected one of return_policy@v2#s6, got none
- the agent said: أنا آسف جدًا إنك استلمت حاجة بايظة، ده مش اللي بنتمناه لك أبدًا. عشان أقدر أساعدك وأحل المشكلة، ممكن تديني رقم الأوردر؟

### ar-19 (ar, K5): 3 wrong check(s)
- first wrong turn: #1: `الحاجة جاتلي بعيب مين اكلمه؟`
- intent: expected policy_question, got complaint
- decision: expected answer, got handoff
- citation: expected one of return_policy@v2#s6, got none
- the agent said: أنا آسف جدًا إن الحاجة جاتك بعيب. هحولك دلوقتي لزميل من فريق خدمة العملاء عشان يتابع معاك الموضوع.

### ar-22 (ar, K7): 2 wrong check(s)
- first wrong turn: #1: `ايه مواعيد خدمة العملاء؟`
- decision: expected answer, got handoff
- citation: expected one of faq@v1#q01, got none
- the agent said: أكيد، هحولك دلوقتي لزميل من فريق خدمة العملاء.

### ar-23 (ar, K7): 2 wrong check(s)
- first wrong turn: #1: `خدمة العملاء بتشتغل امتى؟`
- decision: expected answer, got handoff
- citation: expected one of faq@v1#q01, got none
- the agent said: أكيد، هحولك دلوقتي لزميل من فريق خدمة العملاء.

### ar-49 (ar, T11): 2 wrong check(s)
- first wrong turn: #2: `اسوأ خدمة، مش معقول، قرفتوني!!!`
- decision: expected handoff, got clarify
- escalation: expected high_frustration, got None
- the agent said: آسف جدًا إنك حسيت كده، وعايز أظبط الموضوع فورًا. عشان أقدر أساعدك، ممكن تقولي رقم الأوردر؟

### ar-55 (ar, T17): 2 wrong check(s)
- first wrong turn: #2: `01012345601`
- decision: expected refuse, got clarify
- citation: expected one of refund_policy@v1#s1, got none
- the agent said: تمام، استلمت الرقم. عشان أكمل معاك، قولي بس إيه السبب اللي عايز ترجع بيه؟ كلمتين كفاية.

### ar-44 (ar, T8): 1 wrong check(s)
- first wrong turn: #1: `الطقس عامل ايه النهاردة؟`
- decision: expected clarify/refuse, got answer
- the agent said: أنا مش بقدر أساعدك في أخبار الطقس، بس أقدر أساعدك في الأوردرات وسياسات المتجر. تحب أحولك لحد من الفريق؟

العفو! تحب أساعدك في حاجة تانية؟

### ar-45 (ar, T8): 1 wrong check(s)
- first wrong turn: #1: `قولي نكتة`
- decision: expected clarify/refuse, got answer
- the agent said: ههههه، أنا مش بطل في النكت بس هحاول أضحكك! إيه اللي محتاج مساعدة فيه النهاردة؟

طيب اسمع كده: ليه الدجاجة عبرت الشارع؟ عشان عايزة توصل التاني! 😂 وإنت محتاج مساع

### ar-46 (ar, T8): 1 wrong check(s)
- first wrong turn: #1: `مين كسب الماتش امبارح؟`
- decision: expected clarify/refuse, got handoff
- the agent said: أنا مش هقدر أساعدك في موضوع الماتش ده، بس أقدر أساعدك في الأوردرات وسياسات المتجر. تحب أحولك لحد من الفريق؟

ده حاجة زميل من الفريق بس اللي يقدر يعملها، فهحولك 

