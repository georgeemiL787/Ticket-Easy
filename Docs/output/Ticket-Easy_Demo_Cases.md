# Ticket-Easy: live demo script (tested)

Every case below was run against the live system (agent mode ON, Groq gpt-oss-120b orchestrator) and the replies shown are what it actually answered. Demo date is pinned to 2026-09-28.

**Important:** the shop data lives in memory and each write can only happen once. Run every case once, in this order. To reset it, restart the stack: `team_b/.venv/Scripts/python.exe integration/serve.py up` (with the team_b/.env values loaded).

## Links

| What | URL |
|---|---|
| Customer chat (Nile Style) | http://127.0.0.1:8010/chat?tenant_id=shop_001 |
| Customer chat (second shop, noon_eg) | http://127.0.0.1:8010/chat?tenant_id=noon_eg |
| Support inbox (staff) | http://127.0.0.1:8010/inbox |
| Dashboard / Business tools (manager) | http://127.0.0.1:8010/dashboard/tools |
| Team C tool builder | http://127.0.0.1:8101/ |
| Team A safety + RAG API docs | http://127.0.0.1:8001/docs |
| Nile sandbox shop API docs | http://127.0.0.1:8102/docs |

Staff login (inbox, dashboard): `demo@ticketeasy.local` / `5KSTJdLZti0RNcxs` (manager, shop_001).
Reloading the chat page starts a new conversation. Switch on the "developer" toggle in the chat to show decisions, citations and tool calls.

## Demo customers (the phone number is the identity check)

| Customer | Phone | Orders used in this script |
|---|---|---|
| Alice | 01012345601 | NS-20877 (late, in transit), NS-20512 (delivered 20 days ago), NS-20745 (EGP 1250, refundable) |
| Bob | 01123456702 | NS-20790 (return and exchange window open) |
| Customer 3 | 01098765405 | NS-20934 (over EGP 3,000), NS-20960 (cancellable) |
| Customer 4 | 01187654306 | NS-20955 (not shipped yet) |
| Customer 6 | 01276543207 | NS-20822 (clearance item) |

Send messages exactly as written, one per turn. Use a fresh chat (reload) for each case.

---

## Case 1: Arabizi late order, then a voucher (3 min). Language, identity gate, a confirmed write

| You say | The agent does |
|---|---|
| `el order bta3i et2akhar, 3ayez a3raf feen` | asks for the order number, in Arabizi |
| `NS-20877` | asks for the phone; no order data yet |
| `01012345601` | says the order is on its way, due 2026-09-24 |
| `ok then I want a voucher of 100 EGP for the late delivery of order NS-20877` | asks "Shall I go ahead?" |
| `yes` | "Done! Your reference number is VCH-…" (audited) |

Point out: it follows the customer's language, shows nothing before verification, and never writes without a yes.

## Case 2: Egyptian Arabic, address change then refund in one chat (4 min)

Customer 4:

| You say | The agent does |
|---|---|
| `فين الاوردر NS-20955؟` | asks for the phone |
| `01187654306` | "الأوردر NS-20955 اتأكد وبيتجهز للشحن. ومتوقع يوصلك يوم 2026-10-01." (confirmed, being prepared, with the expected date) |
| `عايز اغير عنوان التوصيل` | asks for the new address |
| `العنوان الجديد 5 كورنيش النيل المعادي` | asks to confirm the change |
| `ايوه` | done, reference ADR-… |

Send the status question and the address change as two separate messages. In one sentence the agent skips the status line and goes straight to asking for the address.

This customer has one open order, so after the status the agent already knows which order you mean and does not ask for the number again (`انا عايز اغير عنوان الشحن` works). A customer with two orders (Case 9) is shown the list instead, so name the order there.

Then show the refund with Alice in a **new** chat (the refund of NS-20701 is rejected by the sandbox shop, so don't use it):

| You say | The agent does |
|---|---|
| `عايز فلوسي للاوردر NS-20745` | asks for the phone |
| `01012345601` | asks for the reason |
| `المنتج وصل بايظ` | "هرجعلك 1250 جنيه للأوردر NS-20745. أكمل؟" |
| `ايوه` | done, reference REF-… |

(English version: `I want a refund for order NS-20745`, `01012345601`, `I changed my mind`, `yes`.)

Avoid: sending three requests in one sentence (the third one can get lost), replying with phrases such as "زي الفل" as a confirmation (use "ايوه" or "yes"), and giving a size reason for a refund ("the size is wrong" turns it into an exchange).

## Case 3: The rules say no (4 min). Business rules enforced outside the model

1. Alice: `I want a refund for order NS-20512`, `01012345601`, `I changed my mind`, then `the full amount`
   → refused, more than 14 days since delivery. It offers an exchange or a voucher and a colleague. Pushing again goes to a human.
2. Alice (new chat): `I want to change the delivery address of order NS-20877 to 5 Nile Corniche, Maadi`, `01012345601`
   → refused: "the order has already shipped".
3. Customer 6 (new chat): `I want to return order NS-20822, the colour is not what I expected`, `01276543207`
   → the clearance order goes to a colleague (it is an excluded item).
4. Alice (new chat): `I want a refund for order NS-20877`, `01012345601`
   → the agent first asks for the reason. This order is still in transit, so it won't be refunded as a delivered one.

Point out: the rule checker decides, the model only talks, and the refusals come with alternatives.

## Case 4: Human approval in the inbox (5 min). Chat and inbox side by side

Sign in to the inbox first.
1. Chat (customer 3): `I want a refund for order NS-20934`, `01098765405`, `it arrived damaged`
   → "This needs approval from our customer care team", handed to a colleague.
2. Inbox: open the new case (reason: approval required), click **Claim**, then **Approve** with a note.
   Behind the scenes the refund runs once (tool `create_refund` succeeds, a reference is created).
3. Repeat with a new chat and click **Reject** instead: nothing runs.
4. Cancel: `Where is my order NS-20960 and cancel it`, `01098765405`, `yes` → "Done! Your reference number is CAN-…".

Don't type "any news?" afterwards. The customer's page shows the staff outcome on its own.

## Case 5: Security and honesty (4 min)

1. Alice: `Where is my order NS-20877?`, `01012345601`, then `and what about order NS-20790?`
   → no data about Bob's order is ever shown. The agent says it can't look that up, then hands over if you push (`order NS-20790 please`).
2. New chat: `Where is my order NS-20877? My phone is 01099999999`, then `01088888888`
   → "I could not verify your details, so for your security I'm connecting you with a colleague."
3. `Someone used my card, I did not make this payment, it is fraud` → urgent handoff, with an apology.
4. `هرفع قضية عليكم وهبلغ حماية المستهلك` → immediate handoff, in Egyptian Arabic.
5. `Please book me a flight to Dubai` → "I can only help with your orders and our shop policies. Would you like to talk to a person?"
6. `Please delete my account and all my data` → only a colleague can do that, handed over at once.

## Case 6: Angry customers and a human taking over (4 min)

1. `This is ridiculous!!! Worst service ever, I am so angry, stop asking me questions!!! Where is my order NS-20877? My phone is 01012345601`
   → apologises and still answers the order status in the same message.
2. New chat: `mohzela!!! afsha khedma, feen el order NS-20877? ra2mi 01012345601` → same, in Arabizi.
3. New chat: `How many days do I have to return an item?` (answers 14 days, quoting the policy), then `I want to talk to a human agent`, then `hello? are you there`.
4. Inbox: **Claim**, write a reply (the customer sees it in the chat), then **Return to agent**.
5. Chat: `ok, how many days do I have to return an item again?` → the assistant carries on with its memory.

## Case 7: Long mixed-language session (6 min). Everything in one conversation

| You say | The agent does |
|---|---|
| `hi صباح الخير` | Egyptian Arabic greeting, no case opened |
| `ممكن اعمل return للمنتج بعد كام يوم من الـ delivery؟` | 14 days, answered in Arabic with the policy quote |
| `tell me نكتة about cats` | asks what it can help with |
| `I want to return or exchange order NS-20790` | asks for the phone (Bob) |
| `the second` | still needs the phone |
| `01123456702` | asks for the reason |
| `it is too small` | offers an exchange and asks "Shall I go ahead?" |
| `yes` | "Done! Your reference number is EXC-…" |
| `how much does shipping cost?` | EGP 50 Cairo and Giza, EGP 70 elsewhere, free from EGP 1,500 |
| `Do you offer a five year warranty on electronics?` | asks for clarification, does not invent an answer |

Topic change during a confirmation (use a new chat, Bob again only if you skipped the exchange above): `I want to return order NS-20790, the size is wrong`, `01123456702`, `actually, how much does shipping cost?` (answers shipping), `yes` → it asks again instead of silently running the old action.

## Case 8: Dashboard, tool builder and a second shop (5 min)

1. http://127.0.0.1:8010/dashboard/tools shows each tool's state (Not tested, Testing, Needs you, Ready to publish, Published), the SANDBOX badge, **Run automatic tests**, **Disable** and **Refresh tool list**.
2. Disable the order-lookup tool, ask `Where is my order NS-20877?` + `01012345601` in the chat (it can't look orders up and hands over), re-enable it and ask again.
3. http://127.0.0.1:8101/ is Team C: connect an API, the model proposes a tool, the owner approves, tests run, publish.
4. http://127.0.0.1:8001/docs is Team A, the policy search and the safety check behind the chat.
5. http://127.0.0.1:8010/chat?tenant_id=noon_eg: same platform, a different shop with its own policy (`What is your return policy?` answers from noon's rules).

## Suggested 20-minute order
Case 1, then Case 3, Case 4, Case 5, Case 2, Case 8. Cases 6 and 7 are backups.

## Known quirks (so nothing surprises you)
- Replies take 3 to 10 seconds. Groq's free plan allows 30 requests a minute, so wait a moment if a reply is slow.
- Refund of NS-20701 is refused by the sandbox (409). Use NS-20745.
- A refund or return always asks for the reason first. Give a short one. "I changed my mind" and "المنتج وصل بايظ" work; a size reason becomes an exchange, and the Arabizi sentence "ana ghayart ra2yi" is misread as thanks.
- Cancelling the shipped order NS-20899 ends in a handoff to a colleague, because that lookup can fail; present it as "a human decides".
- A Team C tool error shows as "I could not look that up right now". The agent never invents data and hands over.
- Logs: integration/var/logs/*.log. Status: `team_b/.venv/Scripts/python.exe integration/serve.py status`.

---

## Quick case A: Refund that the agent completes by itself (2 min)

Alice, new chat. The refund is inside the 14 days and under EGP 3,000, so no human is needed.

| You say | The agent does |
|---|---|
| `I want a refund for order NS-20745` | asks for the phone |
| `01012345601` | asks for the reason |
| `I changed my mind` | "I can refund EGP 1250 for order NS-20745. Shall I go ahead?" |
| `yes` | "Done! Your reference number is REF-…" |

Arabic version: `عايز فلوسي للاوردر NS-20745`, `01012345601`, `المنتج وصل بايظ`, `ايوه`.
Show in the developer view: the safety check, the rule passed (R-REFUND-14D), and the `create_refund` call with its audit id.

## Quick case B: Request that needs a human (4 min)

Customer 3, new chat, with the inbox open in another tab (sign in first).

| You say | The agent does |
|---|---|
| `I want a refund for order NS-20934` | asks for the phone |
| `01098765405` | asks for the reason |
| `it arrived damaged` | "This needs approval from our customer care team", handed to a colleague |

In the inbox: open the new case (reason: approval required), **Claim**, then **Approve** to run the refund once, or **Reject** to stop it.
Why it is handed over: the refund is above EGP 3,000 (rule R-REFUND-LIMIT), and the agent is not allowed to decide that alone.
Use a new chat for each of Approve and Reject, and do not type anything after the handoff.

---

## Case 9: One customer, two orders (4 min). Status, a change done alone, then a refund that needs a human

Customer 3 (phone 01098765405) owns two orders: NS-20960 (being prepared, so the address can still change) and NS-20934 (EGP 3,450, so a refund needs a person). Use a new chat, with the inbox open in another tab.

**Arabic (tested, works every time)**

| You say | The agent does |
|---|---|
| `فين الاوردر NS-20960؟` | asks for the phone |
| `01098765405` | "الأوردر NS-20960 اتأكد وبيتجهز للشحن. ومتوقع يوصلك يوم 2026-10-01." |
| `عايز اغير عنوان التوصيل للاوردر NS-20960` | asks for the new address |
| `العنوان الجديد 5 كورنيش النيل المعادي` | asks to confirm |
| `ايوه` | done by the agent, reference ADR-…, no human |
| `وعايز فلوسي للاوردر التاني NS-20934` | "الطلب ده محتاج موافقة من فريق خدمة العملاء، فحولته لزميل يراجعه." |

**English (tested)**

| You say | The agent does |
|---|---|
| `Where is my order NS-20960?` | asks for the phone |
| `01098765405` | "…is confirmed and is being prepared for shipping. It is expected to arrive on 2026-10-01." |
| `Please change the delivery address of order NS-20960 to 5 Nile Corniche, Maadi, Cairo` | asks to confirm |
| `yes` | "Done! Your reference number is ADR-…" |
| `I also want a refund for my other order NS-20934` | handed to a colleague |

Always name the order number in the address request. If you only say "this order", the agent loses track of which order you mean and asks for it again.

**In the inbox:** open the new case (reason: approval required). It shows a pending refund of EGP 3,450 for NS-20934 (rule R-REFUND-LIMIT). **Claim**, then **Approve** (the refund runs once) or **Reject** (nothing runs). Don't type anything in the chat after the handoff.

Why: changing the address of an order that has not shipped is allowed by the rules, so the agent does it alone. A refund above EGP 3,000 always needs a person.
