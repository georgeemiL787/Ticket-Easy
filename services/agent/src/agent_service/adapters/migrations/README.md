# Migrations

Numbered `NNN_name.sql` files, applied in order the first time the database is used (see `adapters/sqlite_store.py`).
Never edit a migration that has been pushed: add a new one.

| Number | What |
|---|---|
| 001 | sessions, traces, cases (done) |
| 002 | turn_facts, tool_call_facts, policy_facts: the dashboard's summary rows (done) |
| 003 | alerts (done) |
| 004 | users and user_tenants (logins, done) |
| 005 | turn_facts gets escalation_kind, agent_mode, agent_moves, agent_fallback (agent counters, done) |
| 006 | turn_facts gets agent_fallback_reason, reviewer_blocked_replies, reviewer_blocked_actions (done) |
| 007 | tenant_bundles and tenant_bundle_active (versioned business configuration, done) |
| 008 | onboarding_drafts (resumable wizard state, ids and statuses only, done) |

The next number is 009.

Numbers must have no gaps: if 002 is not written yet when 003 is, the runner refuses to start, so whoever pushes
second keeps the numbers above and adds their file on top of the other's.
