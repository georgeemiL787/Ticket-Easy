# Launch readiness review

Checked on 2026-10-06 against the launch checklist in `Docs/plan/Ticket-Easy_Implementation_Plan.md` (section 5.6), on
branch `team-b` at the commit after A16. Every line says what was run or read, so each can be repeated.

## Verdict: NOT READY. Do not launch to real customers yet.

Two checklist items fail on the evidence, and three more are only partly shown. The safety side is solid (every safety
test passes, with no forbidden change in the shop). The gaps are about how well the agent understands customers, and
about things that could only be checked by a person or on another computer.

| # | Checklist item | Result |
|---|---|---|
| 1 | All phase goals met and demos working | PARTLY |
| 2 | Safety tests 100%, zero forbidden changes | PASS |
| 3 | Quality targets met in all four language styles | **FAIL** |
| 4 | 9 of 10 handoff briefings usable by a reviewer | PARTLY (no person has scored them) |
| 5 | Inbox and dashboard require login | PASS |
| 6 | Personal data hidden in logs; old data deleted on schedule | PASS |
| 7 | One-command startup works; automatic checks pass | PARTLY (never run on Docker; the first CI run on GitHub is not read) |
| 8 | Written steps for the three operator tasks | PASS (`runbook.md`) |

Also from phase 5.3, speed and resilience: PASS in the in-process run, not yet run against a separate server (see 7).

## Blockers

1. **Quality targets (item 3) are far from met.** The latest evaluation (`reports/eval_2026-10-06.md`, rules only, 200
   conversations) gives: request right 59.2% (target 90%), outcome right 61.5% (target 92%), correct source 32.6%
   (target 95%), reply language 99.0% (target 100%). Arabizi is 40.8% against English 71.4%, a gap of 30 points (target:
   within 5). The agent still fails safe here (it asks or hands off instead of acting), which is why the safety tests
   pass, but too many customers would be asked to repeat themselves or sent to a person.
   Next step: work through the worst topics in the report (K5, K7, K2, T10 and the Arabic and Arabizi order-status
   phrasings), then run the same set with the AI model on (`make demo-full`, or `TEAM_B_LLM=openrouter`): that run has
   never been made. Owner: Track B (understanding, evidence), with Track A for the action topics.
2. **Handoff briefings (item 4) are not scored by a person.** The automatic check passes for every case opened in all 45
   scenarios (`tests/integration/test_sync1_briefings.py`, 45 passed), meaning each briefing has the required parts.
   Whether a reviewer finds 9 of 10 usable needs a human to read 10 of them. Owner: both, 30 minutes.
3. **Docker has never been run on this computer** (the daemon was off). `make demo` is checked only by file tests.
   Run it once on a clean computer before the demo (see item 7).

## Evidence, item by item

### 1. All phase goals met and demos working: PARTLY
- Prompts A1 to A16 and the matching Track B prompts are done and merged on `team-b`.
- Whole test suite after the last merge: 2444 passed (`pytest -q` in `team_b/`); dashboard: typecheck, build and 38 tests
  passed (`npm run build`, `npm test`).
- 45 scenarios (the three example conversations among them) all pass: `tests/integration/test_scenarios.py` (part of
  the 91 passed in the briefings and scenarios run).
- Not done: connecting the real services (phase 6, optional), the quality targets (item 3).

### 2. Safety tests: PASS
- `tests/adversarial` (10 files: injection, fake approvals, amounts above the order total, identity and other people's
  data, obfuscated risk words, flooding and confirmation, broken parts, the shop's rule cases as conversations),
  the safety property test (random conversations, 4 fixed seeds; the CI adds a fifth), the leak scan and the failure
  matrix: **273 passed**, 0 failed.
- "Zero forbidden changes" is measured in the fake shop's audit log, not in what the agent says (the tests count
  changes in the shop). Human approval never overrides a rule denial; that is tested too.
- CI blocks weakening these: `safety.yml` runs on any change to the brain, and `.github/CODEOWNERS` needs two
  approvals on the action checks and the decision log (`docs/ci.md`). **A person must switch on the GitHub branch rules
  listed in `docs/ci.md`**; until then the block is not enforced.

### 3. Quality targets: FAIL (see Blocker 1)
| style | conversations | request | outcome | source | reply language | handoff precision / recall |
|---|---|---|---|---|---|---|
| en | 50 | 71.4% | 73.1% | 43.5% | 100.0% | 85.7% / 100.0% |
| ar | 50 | 57.1% | 59.6% | 21.7% | 100.0% | 75.0% / 100.0% |
| mixed | 50 | 67.3% | 69.2% | 43.5% | 100.0% | 85.7% / 100.0% |
| arabizi | 50 | 40.8% | 44.2% | 21.7% | 96.2% | 71.4% / 83.3% |
| all | 200 | 59.2% | 61.5% | 32.6% | 99.0% | 79.3% / 95.8% |

Source: `python -m team_b eval --set eval/conversations` (rules mode; the AI-model run has not been made). The 200
conversations were written inside the project; the plan asks for native speakers to write or review them, and that
review has not been done either.

### 4. Handoff briefings: PARTLY (see Blocker 2)
- Automatic completeness check on every case from every scenario: passes.
- Missing: a person scoring 10 briefings as usable or not.

### 5. Inbox and dashboard require login: PASS
- `tests/unit/api/test_a_auth.py` (25 tests): the test walks the real route table; every route outside the short public
  list (customer chat, its outbox and events, welcome text, health, the page files, the login itself) answers 401
  without a login. A manager of one business gets 404 on a second business, an agent gets 403 on reassign, decisions and
  the dashboard, cookies are HttpOnly and SameSite=Lax and signed, writes need the CSRF token, five wrong passwords lock
  that email for 15 minutes.
- Passwords are stored as argon2 hashes.
- To do before real use: set `TEAM_B_SECRET_KEY` (otherwise everyone signs in again after each restart) and
  `TEAM_B_COOKIE_SECURE=1` behind https. Check that `TEAM_B_ENABLE_TEST_ADMIN` is not set.
- `/inbox` stays: the dashboard's Escalations page does claim, assign, approve, release, give back and resolve, but it
  cannot write a reply to the customer. So `/inbox` is not removed.

### 6. Personal data in logs; deletion on schedule: PASS
- `brain/redaction.py` hides phone, email, card, one-time code and address before text goes into a trace or a log;
  covered by the unit tests and the leak scan (no order data in any reply or trace before the customer is verified).
- `retention.py` deletes conversations, traces and finished cases older than `TEAM_B_RETENTION_DAYS` (default 90) in a
  loop started with the service; open and claimed cases and their conversations are kept. Tested in
  `tests/unit/brain/test_retention.py` (part of the 2444 passed).
- Not covered: the server's own web-server logs and any backup file you take (they hold the same data as the database);
  see the runbook's backup section.

### 7. One-command startup and automatic checks: PARTLY
- Done: `docker-compose.yml`, `team_b/Dockerfile`, root `Makefile` (`make demo`, `make demo-full`), `.env.example` with
  every setting documented; the file tests `tests/unit/test_a_docker_files.py` pass (26 passed with the load-test unit
  tests). `make demo` also creates the first admin and prints the password once.
- **Not done:** the image was never built and the compose stack never started here, so "starts on a clean computer and
  the three example conversations work in the browser" is not shown. The load test was the in-process runner, not
  Locust against a running container.
- Load and resilience (phase 5.3), in-process, 50 customers at once, each stand-in switched off for 30 s in turn
  (`loadtest/RESULTS.md`): 16,808 messages, endpoint p95 **721 ms** (limit 3,000 ms), the checker says **PASS** (no write
  without a policy answer, no duplicate write, no write for someone else's order, nothing called done without a proven
  write, recovery probe OK). Not measured: the AI-on 6-second limit.
- Automatic checks (`docs/ci.md`): the workflows exist and every command in them passes locally. Their first runs on
  GitHub, and the branch rules, need a person to check on github.com.

### 8. Written steps: PASS
`team_b/docs/runbook.md`: switch off one action, switch the AI off, handle a pile-up of cases, handle an unverified
result, restore a backup, rotate secrets, add a support person.

## What to do, in order
1. Run `make demo` on a computer with Docker and try the three example conversations (30 minutes).
2. Two people read 10 handoff briefings and score them (30 minutes).
3. Raise quality: fix the worst topics, run the evaluation with the AI model on, and have native speakers review the 200
   conversations. Re-run `python -m team_b eval --set eval/conversations`, update this file.
4. Turn on the GitHub branch rules and check the first `ci.yml` and `safety.yml` runs are green.
5. Set the secrets listed in item 5 and the runbook, then run the readiness review again.
