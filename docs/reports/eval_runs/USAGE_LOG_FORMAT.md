# Token usage log format

One JSON object per line (JSONL), appended after every request to an AI model. Switched on by an environment variable
per service; off (no file, no change in behaviour) when it is empty.

| Service | Log path | Run label |
|---|---|---|
| conversation service (`agent_service`) | `AGENT_USAGE_LOG` | `AGENT_USAGE_RUN_LABEL` |
| knowledge service (`knowledge_service`) | `KNOWLEDGE_USAGE_LOG` | `KNOWLEDGE_USAGE_RUN_LABEL` |
| tool service (`tools_service`) | `TOOLS_USAGE_LOG` | `TOOLS_USAGE_RUN_LABEL` |

All three services write exactly the fields below (E1b).

| Field | Meaning |
|---|---|
| `ts` | UTC time of the end of the request, ISO 8601 with milliseconds |
| `service` | `conversation`, `knowledge` or `tools` |
| `run_label` | free text from the run-label setting (which run the line belongs to); empty when unset |
| `job` | what the call was for. Conversation service: `reader`, `orchestrator`, `knowledge`, `reviewer`, `writer`, `writer_egyptian`, `summary`, `judge` (`default` for the general client). Knowledge service: `rule_extraction`, `risk_second_opinion`, `embed_document`, `embed_query`. Tool service: the call kind (`triage`, `generation`, `reconciliation`, `revision`, `suggestion`, ...) |
| `kind` | `chat` or `embedding` |
| `model` | the model id that was asked |
| `provider` | the server host (and port), never a path, query or credentials |
| `status` | `ok`, `rate_limited` (HTTP 429), `error` (any other failure) or `timeout` |
| `http_status` | the HTTP status, or `null` when no answer came back |
| `input_tokens` | the provider's `usage.prompt_tokens`, else `null` |
| `output_tokens` | the provider's `usage.completion_tokens`, else `null` |
| `reasoning_tokens` | `usage.completion_tokens_details.reasoning_tokens` when given, else `null` |
| `cached_tokens` | `usage.prompt_tokens_details.cached_tokens` when given, else `null` |
| `latency_ms` | milliseconds from sending the request to its answer (or failure) |
| `key_index` | position of the key used in the comma list of keys (0 = first), `null` when no key; never the key itself |
| `backup_used` | `true` when this call went to the backup model |
| `estimated` | `true` when the token counts are an estimate and not the provider's own (used for embeddings when the server does not report a count); otherwise `false` |

Rules:
- A missing usage object writes `null`, never `0`. Rate-limited, failed and timed-out requests are written too, with `null` tokens.
- One line per request sent: a retry on the next key, or a fallback to the backup model or another provider, is its own line.
- Never written: prompt text, reply text, customer data, API keys.
- Writing is append-only and never raises. On any error one warning is logged and the run carries on.

Example (conversation service):

```json
{"ts": "2026-10-16T20:41:07.512+00:00", "service": "conversation", "run_label": "presentation_agent_en", "job": "orchestrator", "kind": "chat", "model": "openai/gpt-oss-120b", "provider": "api.groq.com", "status": "ok", "http_status": 200, "input_tokens": 3412, "output_tokens": 188, "reasoning_tokens": 40, "cached_tokens": null, "latency_ms": 1290, "key_index": 0, "backup_used": false, "estimated": false}
```

Summary: `python services/agent/scripts/usage_summary.py <logs...> [--csv out.csv]` groups by service, run label, job and model.

## How each service fills the fields
- **Conversation service:** one line per HTTP request to a model (a retry on the next key and a backup-model call are separate lines). Tokens are the provider's `usage`.
- **Knowledge service:** one line per OpenRouter request (jobs `rule_extraction`, `risk_second_opinion`; free models only; each model tried in turn is its own line) and one line per Ollama `/api/embed` request (`embed_document` at indexing, `embed_query` for each search). Embedding input tokens are Ollama's `prompt_eval_count` (reported, `estimated` false). If the server does not return it, the count is an estimate of characters / 3 and `estimated` is `true`. Embedding lines have `output_tokens` null.
- **Tool service:** one line per provider attempt in the router (job = the call kind: `triage`, `generation`, `revision`, `repair`, ...). Groq and OpenRouter counts come from `usage`; Ollama's from `prompt_eval_count` and `eval_count`. A fallback to the next key or provider is its own line.
