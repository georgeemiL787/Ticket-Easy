# The agent: the agentic system

Rule that holds everywhere: **the AI model proposes, code decides.** Every AI agent below has a rule-based or template fallback, and its output is checked by code before it is used. The AI can raise a safety flag or send a case to a person; it can never clear a flag, choose a tool, or turn a "deny" into an "allow".

## 1. Workflow of one customer message

```mermaid
flowchart TD
    C([Customer message]) --> MEM[Load session + memory]
    MEM --> SUM{{"History Summarizer agent<br/>(AI, checked against the source)"}}
    SUM --> NLU

    subgraph UNDERSTAND [Understanding]
        NLU{{"NLU agent (AI)<br/>language, intents, entities,<br/>yes/no, wants human, frustration"}}
        RULES[Rule-based NLU<br/>safety floor, always runs]
        RULES --> NLU
        NLU -. "invalid / timeout" .-> RULES
    end

    NLU --> RISK[/"Risk screen (the knowledge service)<br/>fraud, legal, medical, abuse"/]
    RISK -->|risk found| HO
    RISK --> HUMAN{Wants a person?}
    HUMAN -->|yes| HO
    HUMAN -->|no| PEND{Waiting for yes/no?}
    PEND -->|yes| CONF[Confirmation handler]
    PEND -->|no| DIS[Disambiguate + merge slots<br/>missing detail -> ask]
    DIS --> PLAN[Planner: route by intent kind<br/>code only, never the AI]

    PLAN -->|policy question| KA
    PLAN -->|order lookup| LK
    PLAN -->|action| ACT

    subgraph KNOW [Knowledge agent]
        KA{{"Query writer (AI)<br/>messy text -> search query"}} --> SRCH[/"Policy search (the knowledge service)"/]
        SRCH --> ANS{{"Answer writer (AI)<br/>reply from passages + citations"}}
        ANS -->|"not grounded"| QUOTE[Quote passages verbatim]
    end

    subgraph LOOKUP [Lookup]
        LK[Identity check: order id + phone] --> GO[/"get_order (the tools service)"/]
        GO --> OWN{Ownership ok?}
    end
    OWN -->|"not the customer's order"| HO

    subgraph ACTION [Action agent: fixed gate order]
        ACT[Slots -> identity -> facts -> ownership -> permission] --> PA
        PA{{"Policy agent (AI)<br/>reads policy passages,<br/>picks allow / require_human / deny"}}
        RC[/"Rule checker (the knowledge service)"/]
        PA --- RC
        PA --> STRICT["Keep the STRICTER of the two"]
        RC --> STRICT
        STRICT -->|deny| REF[Refuse + offer]
        STRICT -->|require_human| APPR[Needs human approval]
        STRICT -->|allow| ASKC[Ask customer to confirm]
        ASKC --> CONF
        CONF --> RECHK[Renewed policy check]
        RECHK --> EXEC[/"Run tool once (the tools service)"/]
        EXEC --> VER{Result verified?<br/>success + audit id + fields}
        VER -->|no| HO
    end

    APPR --> HO
    REF -. "asked again" .-> HO

    HO{{"Handoff agent<br/>briefing: summary, evidence,<br/>past tickets, priority, next step"}} --> INBOX

    subgraph HUMANS [People]
        INBOX[Support inbox] --> APPROVE["Human approval agent<br/>re-reads order, re-checks rules,<br/>never overrides a deny"]
    end
    APPROVE --> EXEC

    KA -. fail .-> HO
    QUOTE --> COMP
    ANS --> COMP
    OWN -->|ok| COMP
    VER -->|yes| COMP
    REF --> COMP

    COMP["Composer: template reply<br/>in the customer's register"] --> RW{{"Rewrite agent (AI)<br/>low-stakes replies only"}}
    RW -->|"adds/drops any number, date, id, link"| TPL[Use template text]
    RW -->|grounded| OUT
    TPL --> OUT
    OUT([Reply + DecisionTrace saved]) --> C
```

Legend: `{{ }}` AI agent, `[/ /]` outside service (the knowledge service / the tools service), `[ ]` deterministic code.

## 2. Who watches the agents (outside the message loop)

```mermaid
flowchart LR
    TR[(Traces, tool calls,<br/>rule answers, cases)] --> AL["Alert engine (every minute)<br/>service_down, action_failing,<br/>unverified_result, knowledge_gap,<br/>slow_replies, escalation_spike,<br/>ai_fallback, queue_backlog"]
    AL --> DASH[Manager dashboard + webhook]
    TR --> EV[Evaluation runner<br/>NLU accuracy, scripted conversations]
    EV --> JD{{"Judge agent (AI)<br/>clarity, politeness, register,<br/>helpfulness 1-5 · advice only"}}
    JD --> CSV[10% sample -> CSV<br/>a person grades, agreement measured]
```

## 3. The agents

| Agent | Type | Job | Guard rail / fallback | Code |
|---|---|---|---|---|
| **NLU agent** | AI + rules | Reads language, intents, entities, yes/no, wants-human, frustration | Rules run first as a floor; AI drops unknown intents, can't invent ids/phones, can raise but not clear flags; one JSON repair, then `rules_fallback` | `understanding/ai_reader.py`, `understanding/rules.py` |
| **History summarizer** | AI + template | Summarizes older messages that no longer fit in memory | Rejected if it holds a number, id or date not in the source; template summary used instead | `replies/summarizer.py` |
| **Knowledge agent** | AI (2 steps) | Writes the search query, then words the answer from retrieved passages | Answer must pass `check_grounded` and cite only retrieved passages; else verbatim quote. Empty twice -> `no_evidence` handoff | `knowledge/ai_steps.py`, `knowledge/answers.py` |
| **Policy agent** | AI + rule checker | Reads policy for a state-changing action and picks allow / require_human / deny | Stricter of AI and rule checker wins; AI can never turn deny/require_human into allow; AI or search down -> a person decides | `adapters/llm_policy_gate.py` |
| **Action agent** | Code | Runs the fixed gate order and calls the tool once, then verifies the result | Writes never retried; unverified outcome -> handoff | `actions/flow.py`, `actions/permissions.py` |
| **Rewrite agent** | AI + template | Makes low-stakes replies sound natural | Never for confirmations, results, refusals; any new/dropped number, date, id, link or citation -> template used | `replies/rewrite.py` |
| **Handoff agent** | Code (+ AI summary) | Builds the briefing for the human: summary, evidence, past tickets, priority, next step | Briefing completeness check on every case | `handoff/cases.py` |
| **Human approval** | Code | Applies a person's approve / reject | Re-reads the order, re-checks rules with the approval; a deny stays a deny; once only | `actions/approval.py` |
| **Alert engine** | Code | Opens and resolves manager alerts from counts, never message text | One open alert per rule+key | `monitoring/alerts.py` |
| **Judge agent** | AI | Scores reply quality for the evaluation report | Advice only; 10% sampled for human grading | `judge.py` |

Outside services: **The knowledge service** supplies policy search, the rule checker and the risk screen. **The tools service** supplies the shop tools (`get_order`, returns, refunds, tickets). In `AGENT_BACKENDS=live` these are real HTTP calls; otherwise the agent uses its own simulated services.
