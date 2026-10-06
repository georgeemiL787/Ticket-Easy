"""Locust load test: 50 concurrent customers playing the demo conversations against a running server.

    # terminal 1: the server, with the demo date and the test-only chaos endpoints
    TEAM_B_FIXED_TODAY=2026-09-28 TEAM_B_ENABLE_TEST_ADMIN=1 TEAM_B_RATE_LIMIT_PER_MIN=100000 make run
    # terminal 2: 50 customers for 2 minutes, with the failures of chaos.sh switched on during the run
    locust -f loadtest/locustfile.py --headless -u 50 -r 10 -t 2m --host http://127.0.0.1:8010 --csv loadtest/out/run
    python -m loadtest.check --url http://127.0.0.1:8010 --csv loadtest/out/run_stats.csv

A customer = one Locust user. Each task plays one conversation of loadtest/demo.py with a fresh conversation id, so
the failures to test are the system's, not the rate limit's. With --chaos-seconds the first user also switches the
stand-in services off for a while, one after the other (see loadtest/README.md).
"""

import os
import random
import time
import uuid

from locust import HttpUser, between, events, task

from loadtest import demo

CHAOS_SECONDS = float(os.environ.get("CHAOS_SECONDS", "0"))  # 0: no failures
CHAOS_PLUGS = ("rule_checker", "safety_screen", "shop", "policy_search")
CHAOS_START_S = float(os.environ.get("CHAOS_START_S", "20"))
_started = time.monotonic()
_chaos_done: set[str] = set()


class Customer(HttpUser):
    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        self.rng = random.Random(uuid.uuid4().int)

    @task
    def conversation(self) -> None:
        self._maybe_chaos()
        chosen = demo.pick(self.rng)
        conversation_id = f"locust-{uuid.uuid4().hex[:12]}"
        for text in chosen.messages:
            with self.client.post(
                f"/v1/conversations/{conversation_id}/messages",
                json={"tenant_id": demo.TENANT, "text": text},
                name="POST /v1/conversations/{id}/messages",
                catch_response=True,
            ) as response:
                if response.status_code != 200:
                    response.failure(f"{response.status_code}: {response.text[:100]}")
                    return
                response.success()
            time.sleep(self.rng.uniform(0.05, 0.3))  # a person reads before the next message

    def _maybe_chaos(self) -> None:
        """One chaos event per plug, started once by whichever user gets here first (CHAOS_SECONDS > 0)."""
        if CHAOS_SECONDS <= 0:
            return
        elapsed = time.monotonic() - _started
        for number, plug in enumerate(CHAOS_PLUGS):
            if plug not in _chaos_done and elapsed >= CHAOS_START_S + number * (CHAOS_SECONDS + 5):
                _chaos_done.add(plug)
                self.client.post("/v1/_test/chaos", json={"plug": plug, "seconds": CHAOS_SECONDS}, name="chaos")


@events.quitting.add_listener
def _fail_slow_runs(environment, **_: object) -> None:  # type: ignore[no-untyped-def]
    """Exit code 1 when the 95th percentile of the message endpoint is above 3 seconds."""
    stats = environment.stats.get("POST /v1/conversations/{id}/messages", "POST")
    if stats is not None and stats.num_requests and stats.get_response_time_percentile(0.95) > 3000:
        environment.process_exit_code = 1
