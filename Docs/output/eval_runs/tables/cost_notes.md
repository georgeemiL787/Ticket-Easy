# Cost notes (17 October 2026)

All files are in `Docs/output/eval_runs/tables/`; they are produced by `build_e6_tables.py` and `build_e7_tables.py`.

## What was actually spent
**$0.** Every model call in this work used a free tier: Groq free plan, OpenRouter `:free` models, and local Ollama. Every
other cost figure means "what the same measured tokens would cost at paid list prices".

## Formulas
- **API cost per 1,000 messages** = sum over the AI jobs of `(input tokens per message x input price + output tokens per message x output price) / 1,000,000 x 1,000`.
  Tokens per message are the measured averages of the agent-mode run on the real knowledge service (180 conversations,
  213 messages, English + Arabic + mixed; `tokens.csv`): orchestrator 5,353 in / 163 out, reviewer 978 / 76, reader 1,305 / 126,
  knowledge 269 / 25, writer 727 / 44, summary 82 / 15. The judge is evaluation-only and is not priced.
- **Monthly cost** = cost per 1,000 messages x 10 (at 10,000 messages a month) or x 100 (at 100,000).
- **Pilot example** = 50,000 messages (50 x the per-1,000 cost) + 2 onboardings.
- **Onboarding** = measured tokens (tool generation 3,980 in / 1,395 out; rule extraction 59,986 / 19,396; embeddings 3,384 local) priced at list price. Tool generation is priced on gpt-oss-120b, rule extraction on the paid price of the Nemotron model the free tier uses.
- **Local hardware, monthly (ESTIMATE)** = hardware price / 36 months + power (kW) x 730 hours x electricity price (default $0.10 per kWh); the rented tier = 730 hours x the hourly rate. Every input, low and high, is in `local_estimates.csv` so it can be changed. Cost per 1,000 messages = monthly cost / (messages per month / 1,000).

## Measured, estimated, hypothetical
| Cell | Status |
|---|---|
| Tokens per message and per conversation, per job, conversation service | measured (provider-reported counts) |
| Query-embedding tokens, document-embedding tokens, rule-extraction and tool-generation tokens, risk second-opinion tokens | measured (embedding counts reported by Ollama, not estimated) |
| Search-embedding tokens per conversation in `tokens_all_services.csv` | estimate: counted from the knowledge service's log in the time window of the three runs |
| Risk second-opinion tokens per conversation | estimate: the evaluation ran with the second opinion switched off; the figure is its cost per message (measured on 40 messages) times the messages per conversation |
| "qwen as orchestrator too" stack | estimate: orchestrator tokens measured on 25 English conversations only |
| Every hardware / electricity / rental number | estimate; throughput (messages per hour a box can serve) was **not measured** |
| Every row with another vendor's model | hypothetical, not tested: quality and speed "not tested" |

## Prices and where they came from (see `prices.csv`)
- Groq, Google, Anthropic and DeepSeek prices were read from the provider's own pricing page on 17 October 2026 (the Groq page was read through a fetch tool: check it before quoting a number to a customer).
- OpenAI's page answered HTTP 403, so the OpenRouter public price list was used (it equals the plan's indicative price). Mistral's page did not name a Small 4 price, and quotes Large without naming the version, so those two rows use the plan's indicative prices from 6 October 2026 and are marked indicative.
- Gemini 3.8 Flash has an introductory price until 31 December 2026; both prices are shown.
- Groq lists qwen3.8-27b at $0.80 / $4.00 per million tokens as a preview model ("for evaluation only"); OpenRouter lists a Qwen3.8 27B at $0.24 / $2.20. The Groq price is used because the measured tokens came from Groq.

## Caveats
- **Tokenizer:** other models may count the same text as more or fewer tokens, so the hypothetical rows are indicative.
- **Local hardware:** Ollama has $0 API cost; the hardware and electricity are estimates from the tier table and the formula above. A starter box is assumed to keep up at 10,000 messages a month; that must be checked before any sale.
- **Quality:** no hypothetical model was run. Nothing here recommends a model on quality.
- **Tool generation** was measured on an example API with 2 operations; a real business will use more tokens.
- **Same code version:** the agent runs were made before the support-hours rule fix and the evaluation-check fixes described in `conversation/README_E4_followup.md`.
