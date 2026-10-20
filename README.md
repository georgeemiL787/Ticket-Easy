# Ticket-Easy

**Plug in your business. Get a customer-service agent that actually does the work.**

Ticket-Easy is an AI agent for Egyptian businesses. It answers customers in their own language, takes real actions on your systems within the limits you set, and hands over to a person, with a full briefing, the moment it should not act.

---

## Watch the demo

| Language | Video | What it shows |
|---|---|---|
| English | [Business demo (English)](https://drive.google.com/file/d/11M3dIk6nNhw7h3NHtOAoz1LTh4T6d6dR/view?usp=sharing) | Ticket-Easy shown to a business owner, narrated in English |
| Arabic | [Business demo (Arabic)](https://drive.google.com/file/d/1nhfriQphCIe3XJleemFjyXSOWCRUhWWf/view?usp=sharing) | The same demo, narrated in Arabic |

---

## Why Ticket-Easy

Every business gets the same messages all day: *where is my order, why did my bill change, can I move my booking, I want a refund.* People answering them is slow and expensive at night. A scripted chatbot breaks on the first real question. A custom build takes months. None of them write the way Egyptians do: Arabic, English, and both in one sentence.

Ticket-Easy does three things a scripted bot does not:

- **It reads your policies and follows them.** It answers from your documents and shows the exact passage it used. If the answer is not there, it says so instead of guessing.
- **It acts, within limits you set.** The platform builds tools from your API description: look up an order, open a ticket, create a return. Every action is checked against your rules first. "Refunds within 14 days" is a wall the agent cannot cross.
- **It knows when to stop.** Fraud signals, regulator complaints, anything outside policy: the case goes to a person with the conversation, the customer's data, what the agent already tried and a suggested next step. Nobody asks the customer to repeat anything.

It works for any business with written policies and customer data. We start with e-commerce and telecom, and a new industry needs a schema and a set of policies, not a new project.

---

## How it works

### System architecture

```mermaid
flowchart LR
  customer["Customer<br/>web chat"] -->|HTTP| agent
  staff["Support staff<br/>inbox"] -->|HTTP| agent
  manager["Manager<br/>dashboard"] -->|HTTP| agent
  subgraph services["Ticket-Easy services"]
    agent["Agent service · port 8010<br/>chat, inbox and dashboard pages"]
    knowledge["Knowledge service · port 8001<br/>policy search, safety screen,<br/>rule checker"]
    tools["Tools service · port 8101<br/>turns an API description<br/>into approved tools"]
  end
  agent -->|"HTTP JSON"| knowledge
  agent -->|"MCP, one process<br/>per customer identity"| tools
  agent -->|"REST: owner pages,<br/>approvals"| tools
  agent --> agentdb[("Agent database<br/>conversations, decision logs,<br/>cases")]
  knowledge --> knowledgedata[("Documents, search index<br/>and approved rules")]
  tools --> toolsdb[("Tools database<br/>proposals and approved tools")]
  knowledge -->|"embeddings"| ollama["Ollama<br/>bge-m3, runs locally"]
  agent -->|HTTPS| models["AI models<br/>Groq"]
  tools -->|HTTPS| models
  tools -->|"HTTP, the owner's<br/>approved connection"| shop["The shop's own API"]
```

Three separate services talk to each other only over the network; none uses another's code. The **agent service** is the only one customers and staff reach, and it also serves the manager dashboard. The **knowledge service** finds policy passages, screens risky messages and checks every action against the approved rules. The **tools service** turns a shop's API description into tools the owner approves, and the agent calls them over MCP. AI models are used for understanding, planning and wording; local embeddings (Ollama with bge-m3) power the search.

### What happens to a message

```mermaid
flowchart TD
  A["Customer message"] --> B["Load the conversation memory"]
  B --> C["Understand: language, intent, details"]
  C --> D{"Risky topic, or the customer<br/>asks for a person?"}
  D -- yes --> H["Hand over to a person"]
  D -- no --> E["The agent decides what to do"]
  E --> F{"What is needed?"}
  F -- "a question" --> G["Find policy passages<br/>and answer only from them"]
  F -- "a look-up" --> I["Read the order<br/>only the customer's own"]
  F -- "a missing detail" --> Q["Ask the customer"]
  F -- "an action" --> J["Checks: identity, ownership,<br/>permission, policy"]
  J --> K{"Allowed?"}
  K -- no --> H
  K -- yes --> L["Customer confirms,<br/>or a person approves"]
  L --> M["Do it once"]
  M --> N["Verify the result"]
  N --> O{"Verified?"}
  O -- no --> H
  O -- yes --> R
  G --> R["Write the reply in the customer's language,<br/>checked against the facts"]
  I --> R
  Q --> R
  R --> S["Save the decision log"]
  H --> T["Briefing and case in the inbox"]
  T --> U["A person claims the case,<br/>reads the briefing"]
  U --> V["Replies, approves or rejects,<br/>or hands back to the agent"]
  V --> S
```

Every message ends in one outcome: an answer, a question, a confirmation, an action, a refusal or a hand-over, and the decision log records why. A write is never repeated automatically: if its result is unclear, the case goes to a person.

### Inside the agent

```mermaid
flowchart TB
  subgraph AI["AI jobs: they propose"]
    reader["Message reader<br/>language, intent, details"]
    planner["Planner<br/>plans, picks one move at a time,<br/>replans"]
    kagent["Knowledge agent<br/>answers only from found passages"]
    reviewer["Reviewer<br/>can only make things stricter"]
    writer["Reply writer"]
    helpers["Handoff summary<br/>and history summarizer"]
  end
  subgraph CODE["Code: it decides"]
    movecheck["Move check<br/>on the menu, limits, arguments"]
    gates["Action gates<br/>identity, ownership, permission,<br/>policy check"]
    confirm["Customer confirmation<br/>or person approval"]
    verify["Result verification"]
    facts["Facts check on every reply<br/>no new number, date, id or link"]
  end
  fallback["Fixed step flow<br/>used when the AI is off, fails<br/>or is refused twice in a row"]
  reader --> planner
  planner -->|"one move: look up, search policy,<br/>propose action, ask, hand over, reply"| movecheck
  movecheck --> gates
  gates --> confirm
  confirm --> verify
  planner --> kagent
  kagent --> facts
  planner --> writer
  writer --> facts
  reviewer -.->|"blocks, never loosens"| facts
  reviewer -.->|"second look at a write"| gates
  movecheck -.->|"refused moves go back"| planner
  movecheck -.->|"gives up"| fallback
  fallback --> gates
  classDef ai fill:#e8f0fe,stroke:#4a6fa5,color:#1b2b45
  classDef code fill:#e6f4ea,stroke:#3b7d4f,color:#12301c
  classDef fb fill:#fdf3e1,stroke:#b07a1f,color:#3b2a08
  class reader,planner,kagent,reviewer,writer,helpers ai
  class movecheck,gates,confirm,verify,facts code
  class fallback fb
```

*AI proposes, code decides.* A move is only a proposal: it never calls a tool, clears a safety flag or skips a check by itself. The planner and the reviewer use the strong model slot, the message reader, the reply writer and the summaries use the small slot, and search uses the local embedding model.[^models] Handoff and the history summaries are checked too: a text that adds a number, date or id that was not in the source is rejected and a fixed template is used.

---

## Run the demo

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/) running. Nothing else: no Python, no Node.

```bash
make demo
```

The first start takes a few minutes. When it is ready it prints three links:

| Link | What you see |
|---|---|
| <http://localhost:8010/chat?tenant_id=shop_001> | the customer chat |
| <http://localhost:8010/inbox> | where staff take over a case |
| <http://localhost:8010/dashboard> | the manager dashboard, already filled with example conversations |

Things to try in the chat (the demo shop is "Nile Style"; it understands English, Egyptian Arabic and Arabizi such as `3ayez araga3 el order`):

- `Where is my order NS-20877?`, then the phone `01012345601`: the agent verifies you and reads the order.
- `What is your return policy?`: it answers from the policy and shows the passage.
- `I want a refund for order NS-20934`, then `01098765405`: a large refund needs a person. Open the **inbox**, claim the case, read the briefing, approve or reject the refund, and watch the dashboard update.

`make demo` answers by rules and needs no internet. `make demo-full` adds a local AI model (about 5 GB the first time, 8 GB of free memory). `make demo-live` runs the agent against the real knowledge service and starts the tools service too; it needs Ollama with `ollama pull bge-m3` done once. `make logs` shows what it does, `make down` stops it (your data is kept), `make reset` deletes what it stored.

The inbox and the dashboard need a sign-in: `make demo` creates `admin@example.com` and prints its password once (lost it? `make urls` shows how to reset). Every setting is explained in `.env.example`. To run everything without Docker, including the business tools in their sandbox and live-demo modes, follow [docs/INTEGRATION.md](docs/INTEGRATION.md).

---

## What it can do

- **Languages:** English, Egyptian Arabic, mixed Arabic-English, and Arabizi.
- **Real actions, with checks and approvals:** look-ups, returns, refunds, cancellations, vouchers, tickets. Writes are confirmed by the customer, large ones approved by a person, and the result is verified before the agent says "done".
- **Hand-over with a full briefing:** the conversation, the customer's data, the policy passages used, what was tried and why it stopped.
- **A manager dashboard:** what was answered, what was done, what the agent could not do, alerts, and which document to add next.
- **Tools generated from an API description:** an owner uploads an OpenAPI file, reviews the proposed tools, tests them in a sandbox and switches them on.

## Built with safety first

- The agent never invents: no policy claim without a passage, no order fact without a result from the shop, no "done" without a verified result.
- Every action passes the same checks in the same order, and a check that cannot run stops the action and hands the case to a person.
- High-risk actions need a person; a person can never turn a "no" from the rules into a "yes".
- Identity is verified before any customer data is touched, and one customer can never read another's order.
- Every answer and every action is logged with the reason.

## Results

Measured on the evaluation set; every figure comes from the [evaluation and cost report](docs/reports/Ticket-Easy_Eval_and_Cost_Report.md) (section 1.2 unless noted).

| What | Result | Source in the report |
|---|---|---|
| Scripted safety scenarios (real refunds, cancellations, vouchers, exchanges) | 83 of 83 pass, 0 unsafe actions | section 1.2 |
| Guardrail cases | 44 of 44, 0 blocked actions executed | section 1.2 |
| Finding the right policy passage (recall@5, held-out questions) | shop_001 89.29%, noon_eg 97.5%, telco_001 82.22% | section 1.2 |
| Understanding the customer (intent), English / Arabic / mixed | 95.0% / 95.0% / 93.3% | section 1.2 |
| Reply in the customer's language | 99.5% | section 1.2 |
| Cost so far | $0 spent; the measured tokens would cost 3.843 USD per 1,000 messages at paid list prices | section 2.6 |

The report also lists the honest gaps against the targets (section 1.7).

## Project layout

| Folder | What is in it |
|---|---|
| [`services/knowledge/`](services/knowledge/README.md) | The knowledge service: documents, search, safety screen, rule checker |
| [`services/agent/`](services/agent/README.md) | The agent service: chat, understanding, decisions, actions, hand-off, inbox |
| [`services/tools/`](services/tools/README.md) | The tools service: API description in, approved tools out |
| [`apps/dashboard/`](apps/dashboard/README.md) | The manager dashboard (React), built into the agent |
| `ops/local/` | Start all services locally and the end-to-end checks |
| `ops/` | `check_names.py` and `check_links.py`, the two repository-wide checks |
| [`contracts/`](contracts/README.md) | The published data formats between the services |
| `docs/` | Guides, plans and reports |
| `docker-compose.yml`, `Makefile` | The one-command demo |

## For developers

- Each service has its own README: [knowledge](services/knowledge/README.md), [agent](services/agent/README.md), [tools](services/tools/README.md), [dashboard](apps/dashboard/README.md).
- [docs/INTEGRATION.md](docs/INTEGRATION.md): how the three services work together and how to run them locally.
- [contracts/README.md](contracts/README.md): the published data formats.
- [docs/ownership.md](docs/ownership.md): who looks after what. [docs/reports/](docs/reports/Ticket-Easy_Eval_and_Cost_Report.md) and `docs/plan/`: the reports and plans.
- Checks that run on every pull request: three test suites, the dashboard build, the JSON schemas, the scenarios, and two repository-wide checks you can run yourself: `python ops/check_names.py` and `python ops/check_links.py`.

## Where we are

Concept and proof-of-concept stage. The first demo takes one business from documents to a working, policy-bound agent, and onboards a second industry from a schema in under an hour. We are looking for pilot partners, builders who like agents that take real actions safely, and advisors who have run customer operations in Egypt.

---

*Ticket-Easy: your policies, your data, one agent that follows both.*

## People

- George Emil ([@georgeemiL787](https://github.com/georgeemiL787))
- Asmaa Nazeh ([@Asmaa-Nazeh](https://github.com/Asmaa-Nazeh))
- Omar Essam ([@omarEssam-11](https://github.com/omarEssam-11))
- Basel Elhofy ([@Basel-Elhofy](https://github.com/Basel-Elhofy))
- Basmala Hesham ([@pasmala2004](https://github.com/pasmala2004))

[^models]: Which model runs each job is set in the agent's settings (`AGENT_LLM_MODEL_<JOB>`); the minimum stack and its cost are in section 2.1 of the [evaluation and cost report](docs/reports/Ticket-Easy_Eval_and_Cost_Report.md).
