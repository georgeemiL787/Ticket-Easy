# Migrations

Numbered `NNN_name.sql` files, applied in order the first time the database is used (see `adapters/sqlite_store.py`).
Never edit a migration that has been pushed: add a new one.

| Number | What | Owner |
|---|---|---|
| 001 | sessions, traces, cases (done) | B |
| 002 | turn_facts, tool_call_facts, policy_facts: the dashboard's summary rows (done) | B |
| 003 | alerts | A |
| 004 | users and user_tenants (logins) | A |
| 005+ | ask the other person first | - |

Numbers must have no gaps: if 002 is not written yet when 003 is, the runner refuses to start, so whoever pushes
second keeps the numbers above and adds their file on top of the other's.
