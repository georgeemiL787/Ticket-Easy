# Who owns what

Ticket-Easy is built by five people. This page says who looks after which service and which part of the agent. It is
read from the commit history and the code-owner file, so correct it when the work moves.

## People

| Person | GitHub |
|---|---|
| George Emil | [@georgeemiL787](https://github.com/georgeemiL787) |
| Asmaa Nazeh | [@Asmaa-Nazeh](https://github.com/Asmaa-Nazeh) |
| Omar Essam | [@omarEssam-11](https://github.com/omarEssam-11) |
| Basel Elhofy | [@Basel-Elhofy](https://github.com/Basel-Elhofy) |
| Basmala Hesham | [@pasmala2004](https://github.com/pasmala2004) |

## Services

| Service | Folder | What it does | Looked after by |
|---|---|---|---|
| Knowledge | `services/knowledge/` | Finds the right policy passage with a citation, screens risky messages, and checks every action against the approved rules | Basmala Hesham (most of its commits), with Omar Essam and George Emil |
| Agent | `services/agent/` | Chat, understanding, decisions, actions, hand-off to a person, inbox, and the manager dashboard pages | Omar Essam and Basel Elhofy (see the parts below) |
| Tools | `services/tools/` | Turns a shop's API description into tools the owner approves, and serves them to the agent | George Emil (most of its commits), with Omar Essam |
| Dashboard | `apps/dashboard/` | The manager dashboard (React), built into the agent | Omar Essam, with George Emil and Basel Elhofy |
| Local scripts and docs | `ops/local/`, `docs/` | Starting everything locally, the end-to-end checks, the plans and reports | Shared |

## Parts of the agent

The agent was split between two people. The folders below are inside `services/agent/src/agent_service/`.

| Part | What it does | Looked after by |
|---|---|---|
| `actions/` | Identity check, look-ups, refunds and cancellations, permissions, approvals | Omar Essam |
| `monitoring/` (alerts), safety tests under `tests/adversarial/`, load tests, logins, Docker | Alerts, attacks on the agent, load and failure runs, sign-in, the demo containers | Omar Essam |
| `knowledge/` | Policy and FAQ answers built from the passages the knowledge service finds | Basel Elhofy |
| `handoff/` | The briefing a person receives, with the full conversation and what was tried | Basel Elhofy |
| `monitoring/` (numbers), `evaluation/`, inbox and dashboard pages, CI | Dashboard numbers, the evaluation runs, the inbox, the automatic checks | Basel Elhofy |
| `turn/`, `understanding/`, `agent/`, `replies/` | Running a message, reading it, the AI planner, writing the reply | Shared |

## Who must review what

The code that decides whether the shop is touched needs two reviewers, and all five people are code owners for it:
the action flow (`actions/flow.py`), the action state machine (`domain/actions.py`), the trace rules
(`domain/trace.py`), the rule checker simulation (`adapters/simulated/rule_checker.py`), and the automatic checks in
`.github/`. See `.github/CODEOWNERS` and `services/agent/docs/ci.md`.

Asmaa Nazeh has no commits in the history yet; she reviews as a code owner.
