# Last measured results

Laptop (Windows 11, Python 3.12), rules mode (no AI model), stand-ins, in-process runner.
Command: `make chaos` = `python -m loadtest.run --customers 50 --conversations 150 --chaos rule_checker:30@5 --chaos safety_screen:30@35 --chaos shop:30@65 --chaos policy_search:30@95`

| | |
|---|---|
| Customers at once | 50 |
| Messages | 16,808 in 173 s (97 per second) |
| Endpoint `POST /v1/conversations/{id}/messages` | **p50 424 ms, p95 721 ms** (limit 3,000 ms) |
| One whole turn (from the traces) | p50 4.9 ms, p95 14.2 ms |
| Slowest stages (p95) | handoff 9.2 ms, merge 7.4 ms, understand 4.4 ms, risk_screen 2.0 ms |
| Failures | each stand-in off for 30 s, one after another |
| What the agent did | 1,914 turns ended in a handoff `dependency_unavailable`, 129 `approval_required`, 424 `policy_denied`; 52 changes executed |
| Rate-limited answers | 0 (limit raised for the test) |
| Checker | **PASS**: no write without a policy answer, no duplicate write, no write for someone else's order, nothing called done without a proven write, no write while a check could not run, recovery probe OK |

The client-side time is dominated by 50 customers sharing one process and one event loop; a single turn takes a few
milliseconds. No bottleneck stands out: `understand` (reading the message) and `risk_screen` are the two stages that
take more than a millisecond at the median, `merge` has the longest tail (its p95 is the free-text and order-number
handling for a few long messages). Nothing needed optimising to stay under the 3 s limit.

Not measured here: Locust against a separate server process (use `loadtest/README.md`), and the AI model modes
(`TEAM_B_LLM=ollama|openrouter`), where the model's own time adds to every turn.
