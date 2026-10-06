# Ticket-Easy

**Plug in your business. Get a customer-service agent that actually does the work.**

Ticket-Easy is an AI agent for Egyptian businesses that answers customers in their own language, takes real actions on your systems, and knows exactly when to hand over to a human. You give it your policies and your data. It gives you a support team that never sleeps and never makes something up.

---

## Run the demo

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/) running. Nothing else: no Python, no Node.

```bash
make demo
```

The first start takes a few minutes (it builds the service and the dashboard). When it is ready it prints three links:

| Link | What you see |
|---|---|
| <http://localhost:8010/chat?tenant_id=shop_001> | the customer chat. Try: `Where is my order NS-20877?` then the phone `01012345601`; `What is your return policy?`; or `I want a refund for order NS-20934` then `01098765405` (a large refund needs a person) |
| <http://localhost:8010/inbox> | where the support team takes over: claim the case from the chat above, read the briefing, approve or reject the refund |
| <http://localhost:8010/dashboard> | the manager dashboard, already filled with example conversations from the last two weeks, with the alerts page |

The demo shop is "Nile Style". It speaks English, Egyptian Arabic and Arabizi (Arabic written in Latin letters, like `3ayez araga3 el order`).
This version answers by rules and needs no internet. For the version where an AI model also helps to understand messages, run
`make demo-full` instead (it downloads a model of about 5 GB the first time and needs a computer with 8 GB of free memory).

Other commands: `make logs` (watch what it does), `make down` (stop it; your conversations are kept), `make reset` (stop and
delete everything it stored). Every setting is explained in `.env.example`; copy it to `.env` to change one.
The inbox and the dashboard have no sign-in yet (it comes with the accounts step), so only run the demo on your own computer.

---

## The problem

Every business in Egypt gets the same messages, all day, on WhatsApp, Instagram and web chat: *where is my order, why did my bill change, can I move my booking, I want a refund.*

Today the choices are bad:

- **Hire people** to answer the same questions on shifts. Expensive, slow at night, inconsistent.
- **Buy a chatbot** that follows a script. Customers ask one real question, it breaks, they type "human", staff switch it off.
- **Build something custom** with an agency. Months of work, every policy change is a new invoice.

And none of them speak the way Egyptians actually write: Arabic, English, and both mixed in the same sentence.

## The idea

Ticket-Easy does three things no scripted bot does:

**1. It reads your policies and follows them.**
Upload your return policy, your FAQ, your tariff sheet. The agent answers from those documents and shows the customer the exact passage it relied on. If the answer is not in your documents, it says so instead of guessing.

**2. It acts, within limits you set.**
Give it your data structure and the platform builds the tools itself: look up an order, open a ticket, create a return, change a plan. Every action is checked against your rules first. "Refunds within 14 days" is not a suggestion to the agent; it is a wall it cannot cross.

**3. It knows when to stop.**
Fraud signals, regulator complaints, safety issues, anything outside policy: the case goes to a human immediately, with the full conversation, the customer's data, what the agent already tried, and a suggested next step. Your team picks up without asking the customer to repeat anything.

## Why it is different

| Scripted chatbots | Ticket-Easy |
|---|---|
| Follow a flow someone drew | Read your actual policy documents |
| Break on unexpected questions | Answer from your knowledge or admit they cannot |
| Talk only | Look up, create and update records |
| Need a developer for every change | Upload a new document; the agent updates |
| Formal Arabic or English only | Egyptian Arabic, English, and Arabizi mixed |
| Escalate a bare transcript | Escalate a full briefing a human can act on |

## Who it is for

Any business with written policies and customer data. We start where the message volume, and the pain, is highest:

- E-commerce and online retail
- Telecom, ISPs and subscription services
- Fintech and lending
- Hospitality and travel
- Airlines

The same engine works for a new industry with a schema and a set of policies, not a new project.

## What a customer experiences

> **Customer, 11 pm, WhatsApp:** "el order mesh wesel lel delivery w ana 3ayez a3raf feen"
>
> **Ticket-Easy:** verifies the phone number and order id, pulls the live status, explains the delivery policy with the passage it came from, and offers to open a ticket with the courier. Done in one exchange.
>
> The next customer asks for a refund on day 20. The agent explains the 14-day rule, cites it, and passes the case to a human with everything attached. The human approves an exception in two clicks. The customer is answered in the same thread.

## What a business owner experiences

Upload documents. Connect a data source. Review the tools the platform proposes and switch on the ones you want. Set your tone and rules in plain language. Test-chat. Go live. Afterwards, one screen shows what the agent answered, what it did, what it could not do, and which document you should upload next.

No flows to draw. No scripts to write. No developer on call.

## Built with safety first

- The agent never acts without passing a policy check it cannot override.
- High-risk actions default to human approval.
- Every answer and every action is logged and traceable to its source.
- Identity is verified before any customer data is touched.

## Where we are

Concept and proof-of-concept stage. A five-person team is building the core: the agent, the knowledge engine, the policy guardrails, the tool generator, and the human hand-off. The first demo shows one business going from documents to a working, policy-bound agent, and a second industry onboarded from a schema in under an hour.

## Join us

We are looking for:

- **Pilot partners**: a business willing to put the agent in front of real customers on one use case and help shape it for their industry.
- **Builders**: engineers who want to work on agents that take real actions safely, Arabic-first retrieval, and policy-as-code.
- **Advisors**: people who have run customer operations in Egypt and know where bots fail.

If any of that is you, let's talk.

---

*Ticket-Easy: your policies, your data, one agent that follows both.*

## Team

- George Emil ([@georgeemiL787](https://github.com/georgeemiL787))
- Asmaa Nazeh ([@Asmaa-Nazeh](https://github.com/Asmaa-Nazeh))
- Omar Essam ([@omarEssam-11](https://github.com/omarEssam-11))
- Basel Elhofy ([@Basel-Elhofy](https://github.com/Basel-Elhofy))
- Basmala Hesham ([@pasmala2004](https://github.com/pasmala2004))
