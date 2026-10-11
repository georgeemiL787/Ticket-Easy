# Load and chaos tests

Two questions: **how fast is it** with 50 customers at once, and **does it stay safe and recover** when the parts it depends
on fail. Everything runs on the stand-ins (no AI model: rules mode).

## The quick way (no extra install)

```bash
make loadtest                      # 50 customers, no failures
make chaos                         # 50 customers, each stand-in switched off for 30 seconds, one after another
python -m loadtest.run --customers 50 --conversations 150 \
    --chaos rule_checker:30@5 --chaos safety_screen:30@35 --chaos shop:30@65 --chaos policy_search:30@95
```

`--chaos PLUG:SECONDS@START` switches the stand-in off START seconds into the run for SECONDS seconds; it heals itself, nobody
restarts anything. The runner starts the app in the same process, plays the demo conversations (`loadtest/demo.py`: status
lookups, policy questions, confirmed returns, exchanges and address changes, vouchers, a refund that needs a person,
greetings, in English, Egyptian Arabic and Arabizi), then prints:

* p50 / p95 per endpoint (what a customer waits for) and per stage of a turn (from the decision traces)
* what the agent did when parts failed (decisions and handoff reasons)
* the checker's verdict: **PASS**, or the list of problems. Exit code 1 on a problem or when p95 is above 3 s.

## With Locust (a running server)

```bash
pip install -r loadtest/requirements.txt
# terminal 1: the demo date, the test-only endpoints and no rate limit
TEAM_B_FIXED_TODAY=2026-09-28 TEAM_B_ENABLE_TEST_ADMIN=1 TEAM_B_RATE_LIMIT_PER_MIN=100000 make run
# terminal 2: 50 customers for 3 minutes; CHAOS_SECONDS=30 also switches each stand-in off for 30 s, one by one
CHAOS_SECONDS=30 locust -f loadtest/locustfile.py --headless -u 50 -r 10 -t 3m \
    --host http://127.0.0.1:8010 --csv loadtest/out/run
# when it has finished and the last failure has healed (about 30 s later):
python -m loadtest.check --url http://127.0.0.1:8010 --csv loadtest/out/run_stats.csv
```

`TEAM_B_ENABLE_TEST_ADMIN=1` adds `/v1/_test/*` (switch a plug off for N seconds, read the shop audit log and the traces).
It must never be set in production. Without it those URLs do not exist.

## What the checker proves (loadtest/checker.py)

It trusts nothing the agent said. It reads the shop's own audit log and the decision traces and fails when:

1. a write has no policy answer in its trace, or went ahead after a deny, or needed a person's approval and had none
2. an idempotency key was applied twice, or an order was refunded twice
3. a write touched an order that is not the verified customer's, or a refund is not the order total
4. a customer was told "done" without a proven write (success, audit id, reference id)
5. a change was made although the rule checker or safety screen could not answer in that turn, or an unclear write
   was not handed to a person (`unverified_result`)
6. after the failures, ordinary requests do not work again (a recovery probe: a policy answer, an order lookup and a
   checked action) — recovery without a restart

## Reading the numbers

The client-side p95 of a run with 50 customers at once includes waiting for the other 49 (one process, one event loop), so
it is higher than the time one turn takes. The per-stage table shows the work itself: a whole turn is a few milliseconds
and most of it is understanding the message and the safety screen. If a number gets worse, the stage table says where.

Last measured numbers are in `loadtest/RESULTS.md`.
