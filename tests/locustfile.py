"""
Distributed Bookshop - End-to-End Load Test Suite
Seminar 14 | Distributed Systems, University of Tartu

Run with:
    locust -f locustfile.py --host http://localhost:8080

For headless execution (no browser UI):
    locust -f locustfile.py --host http://localhost:8080 --headless -u <users> -r <spawn_rate> -t <duration>

Scenario classes:
    SingleOrderUser         – one non-fraudulent order (baseline correctness)
    NonConflictingUser      – multiple simultaneous orders for different books
    MixedOrderUser          – mix of fraudulent and non-fraudulent orders
    ConflictingOrderUser    – concurrent orders for the same book (conflict)
    FailoverUser            – leader executor killed mid-test; measures re-election + recovery

To run only a specific scenario:
    locust -f locustfile.py --host http://localhost:8080 --headless -u 5 -r 1 -t 30s SingleOrderUser
"""

import json
import random
import subprocess
import threading
import time
from locust import HttpUser, task, between, events, constant

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

# Books that are known to be in the catalogue (from books_database/src/app.py DEFAULT_CATALOGUE)
CATALOGUE_BOOKS = [
    "100 Years of Solitude",
    "Feast of the Goat",
    "The House of Spirits",
    "Brothers Karamazov",
    "Les Misérables",
    "The Iliad",
    "Lord of the Rings",
    "The Name of the Wind",
    "The Chronicles of Narnia",
    "Dune",
    "Neuromancer",
    "Foundation",
    "Midnight's Children",
    "God of Small Things",
    "The Grapes of Wrath",
]

# A single book used to create *conflicting* orders
CONFLICT_BOOK = "Dune"

# Card numbers / expiry dates that trigger fraud detection in the project
# (the fraud service flags cards expiring before 2024 as expired)
FRAUDULENT_CARD = {
    "number": "1234-5678-9012-3456",
    "expirationDate": "01/20",   # expired → should trigger fraud
    "cvv": "123",
}

VALID_CARD = {
    "number": "4111-1111-1111-1111",
    "expirationDate": "12/28",
    "cvv": "123",
}


def make_order(book_names: list[str], card: dict | None = None, user_name: str = "Test User") -> dict:
    """Build a checkout request payload."""
    if card is None:
        card = VALID_CARD
    return {
        "user": {
            "name": user_name,
            "contact": "test@example.com",
        },
        "creditCard": card,
        "items": [{"name": name, "quantity": 1} for name in book_names],
        "billingAddress": {
            "street": "123 Test Street",
            "city": "Tartu",
            "state": "EE",
            "zip": "51003",
            "country": "Estonia",
        },
        "termsAndConditionsAccepted": True,
    }


# ---------------------------------------------------------------------------
# Scenario 1 – Single non-fraudulent order
# ---------------------------------------------------------------------------

class SingleOrderUser(HttpUser):
    """
    Sends one valid order at a time with a long wait between requests.
    Goal: verify the happy-path flow end-to-end and measure baseline latency.
    """
    wait_time = between(3, 7)

    @task
    def single_valid_order(self):
        book = random.choice(CATALOGUE_BOOKS)
        payload = make_order([book])
        with self.client.post(
            "/checkout",
            json=payload,
            catch_response=True,
            name="[S1] Single non-fraudulent order",
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"Unexpected status {resp.status_code}")
                return
            data = resp.json()
            status = data.get("status", "")
            if status != "Order Approved":
                resp.failure(f"Expected 'Order Approved', got '{status}'")
            else:
                resp.success()


# ---------------------------------------------------------------------------
# Scenario 2 – Multiple non-conflicting orders (different books)
# ---------------------------------------------------------------------------

class NonConflictingUser(HttpUser):
    """
    Simulates multiple concurrent users, each purchasing a *different* book.
    Books are assigned round-robin per user so no two users share a title.
    Goal: measure throughput and verify no cross-order interference.
    """
    wait_time = between(1, 3)
    # Each virtual user picks a unique offset into the catalogue
    _user_counter = 0

    def on_start(self):
        # Rotate through the catalogue so concurrent users get different books
        self._book = CATALOGUE_BOOKS[
            NonConflictingUser._user_counter % len(CATALOGUE_BOOKS)
        ]
        NonConflictingUser._user_counter += 1

    @task
    def non_conflicting_order(self):
        payload = make_order([self._book])
        with self.client.post(
            "/checkout",
            json=payload,
            catch_response=True,
            name="[S2] Non-conflicting order",
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"Unexpected status {resp.status_code}")
                return
            data = resp.json()
            if data.get("status") != "Order Approved":
                resp.failure(f"Expected approved, got: {data.get('status')}")
            else:
                resp.success()


# ---------------------------------------------------------------------------
# Scenario 3 – Mixed orders (fraudulent + non-fraudulent)
# ---------------------------------------------------------------------------

class MixedOrderUser(HttpUser):
    """
    Alternates between legitimate and fraudulent checkout requests.
    Goal: verify that fraudulent orders are rejected and valid ones approved,
    and that one does not affect the other under concurrent load.
    """
    wait_time = between(1, 4)

    @task(3)
    def valid_order(self):
        book = random.choice(CATALOGUE_BOOKS)
        payload = make_order([book], card=VALID_CARD)
        with self.client.post(
            "/checkout",
            json=payload,
            catch_response=True,
            name="[S3] Valid order (mixed)",
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"HTTP {resp.status_code}")
                return
            if resp.json().get("status") != "Order Approved":
                resp.failure(f"Valid order rejected: {resp.json().get('status')}")
            else:
                resp.success()

    @task(1)
    def fraudulent_order(self):
        book = random.choice(CATALOGUE_BOOKS)
        payload = make_order([book], card=FRAUDULENT_CARD, user_name="Fraud User")
        with self.client.post(
            "/checkout",
            json=payload,
            catch_response=True,
            name="[S3] Fraudulent order (mixed)",
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"HTTP {resp.status_code}")
                return
            status = resp.json().get("status", "")
            # We *expect* this to be rejected
            if status == "Order Rejected":
                resp.success()
            else:
                # If the fraudulent order slips through, mark as failure
                resp.failure(f"Fraudulent order was NOT rejected: {status}")


# ---------------------------------------------------------------------------
# Scenario 4 – Conflicting orders (same book, concurrent users)
# ---------------------------------------------------------------------------

class ConflictingOrderUser(HttpUser):
    """
    All virtual users attempt to purchase the exact same book simultaneously.
    Goal: stress-test the executor's conflict resolution / 2PC logic and
    confirm that at most one order succeeds (or all succeed if the system
    allows concurrent stock without depletion in your current implementation).
    """
    wait_time = constant(0)   # fire as fast as possible to maximise conflict

    @task
    def conflicting_order(self):
        payload = make_order([CONFLICT_BOOK])
        with self.client.post(
            "/checkout",
            json=payload,
            catch_response=True,
            name="[S4] Conflicting order (same book)",
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"HTTP {resp.status_code}")
                return
            # Both Approved and Rejected are valid outcomes here;
            # what matters is that the system responds consistently.
            status = resp.json().get("status", "")
            if status in ("Order Approved", "Order Rejected"):
                resp.success()
            else:
                resp.failure(f"Unexpected status: {status}")


# ---------------------------------------------------------------------------
# Scenario 5 – Leader failover under load
# ---------------------------------------------------------------------------

# Shared state written by the failover orchestrator thread,
# read by FailoverUser instances to tag requests correctly.
_failover_state = {
    "phase": "baseline",        # baseline → killing → dead → recovering → recovered
    "kill_time": None,          # wall-clock time when container was stopped
    "recover_time": None,       # wall-clock time when first post-restart approval arrived
    "leader_endpoint": None,    # filled in by querying GET /leader on the orchestrator
    "lock": threading.Lock(),
}

# Container name to stop/start. The Bully algorithm elects highest ID as
# leader, so executor3 is the default leader when all replicas are up.
# If your service exposes GET /leader the failover thread will update this
# dynamically; otherwise it falls back to the value below.
_DEFAULT_LEADER_CONTAINER = "executor3"


def _get_current_leader(host: str) -> str:
    """
    Query GET /leader on the orchestrator to find the current leader container
    name. Falls back to _DEFAULT_LEADER_CONTAINER if the endpoint does not
    exist yet (pre-implementation).
    """
    import urllib.request
    import urllib.error
    try:
        with urllib.request.urlopen(f"{host}/leader", timeout=3) as r:
            data = json.loads(r.read())
            return data.get("leader", _DEFAULT_LEADER_CONTAINER)
    except Exception:
        return _DEFAULT_LEADER_CONTAINER


def _docker(command: str, container: str) -> bool:
    """Run `docker <command> <container>` and return True on success."""
    result = subprocess.run(
        ["docker", command, container],
        capture_output=True,
        text=True,
    )
    ok = result.returncode == 0
    status = "OK" if ok else f"FAILED – {result.stderr.strip()}"
    print(f"\n[S5] docker {command} {container} → {status}")
    return ok


def _failover_sequence(host: str, kill_after: float, restart_after: float):
    """
    Background thread that drives the kill → restart lifecycle.

    Timeline (seconds from test start):
        0               – baseline orders flowing normally
        kill_after      – kill leader; enter failure window
        kill_after + restart_after – restart leader; wait for recovery
    """
    time.sleep(kill_after)

    with _failover_state["lock"]:
        leader = _get_current_leader(host)
        _failover_state["leader_endpoint"] = leader
        _failover_state["phase"] = "killing"

    print(f"\n[S5] ── PHASE: killing leader '{leader}' ──")
    _docker("stop", leader)

    with _failover_state["lock"]:
        _failover_state["kill_time"] = time.time()
        _failover_state["phase"] = "dead"

    time.sleep(restart_after)

    with _failover_state["lock"]:
        _failover_state["phase"] = "recovering"

    print(f"\n[S5] ── PHASE: restarting '{leader}' ──")
    _docker("start", leader)

    with _failover_state["lock"]:
        _failover_state["phase"] = "recovered"

    print(f"\n[S5] ── PHASE: recovered – waiting for first successful order ──")


class FailoverUser(HttpUser):
    """
    Continuously sends valid orders while a background thread kills and
    restarts the leader executor.

    Phases tracked per-request:
        [S5-baseline]   – before kill; all should be approved
        [S5-dead]       – during failure window; some orders may be delayed
                          or rejected depending on queue/retry behaviour
        [S5-recovering] – executor restarting; re-election in progress
        [S5-recovered]  – post-recovery; all should be approved again

    The recovery time (first approved order after restart) is printed at the
    end of the test. Compare it with your Bully election timeout to verify
    the algorithm is working within expected bounds.

    Run with at least 3 users so there is always load during the dead window:
        locust -f locustfile.py --host http://localhost:8080 --headless \
               -u 3 -r 3 -t 90s FailoverUser
    """
    wait_time = between(1, 2)
    _sequence_started = False
    _sequence_lock = threading.Lock()

    # Timings (seconds). Adjust to match your Bully timeout if needed.
    KILL_AFTER    = 15   # seconds of baseline before killing leader
    RESTART_AFTER = 20   # seconds the leader stays dead before restart

    def on_start(self):
        # Only the first user instance launches the failover thread.
        with FailoverUser._sequence_lock:
            if not FailoverUser._sequence_started:
                FailoverUser._sequence_started = True
                t = threading.Thread(
                    target=_failover_sequence,
                    args=(self.environment.host, self.KILL_AFTER, self.RESTART_AFTER),
                    daemon=True,
                )
                t.start()

    def _current_phase(self) -> str:
        with _failover_state["lock"]:
            return _failover_state["phase"]

    @task
    def order_during_failover(self):
        phase = self._current_phase()
        name  = f"[S5-{phase}] order"
        book  = random.choice(CATALOGUE_BOOKS)
        payload = make_order([book])

        with self.client.post(
            "/checkout",
            json=payload,
            catch_response=True,
            name=name,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"HTTP {resp.status_code}")
                return

            status = resp.json().get("status", "")

            if phase in ("baseline", "recovered"):
                # Outside the failure window every order must be approved.
                if status == "Order Approved":
                    resp.success()
                else:
                    resp.failure(f"Expected approved in phase '{phase}', got '{status}'")

            elif phase in ("dead", "recovering"):
                # During failure window the system may approve or reject
                # (depends on queue behaviour). Both are acceptable; we just
                # want responses — not crashes.
                if status in ("Order Approved", "Order Rejected"):
                    resp.success()
                    # Record the first successful approval after restart.
                    if (
                        phase == "recovering"
                        and status == "Order Approved"
                        and _failover_state["recover_time"] is None
                    ):
                        with _failover_state["lock"]:
                            if _failover_state["recover_time"] is None:
                                _failover_state["recover_time"] = time.time()
                                kill_t = _failover_state["kill_time"]
                                elapsed = _failover_state["recover_time"] - kill_t if kill_t else "?"
                                print(
                                    f"\n[S5] ✓ First approved order after restart "
                                    f"– recovery time: {elapsed:.1f}s"
                                )
                else:
                    resp.failure(f"Unexpected status in phase '{phase}': {status}")

            else:
                # 'killing' is transient; treat like dead
                resp.success() if status in ("Order Approved", "Order Rejected") \
                    else resp.failure(f"Unexpected: {status}")


# ---------------------------------------------------------------------------
# Event hooks – print a summary banner at test end
# ---------------------------------------------------------------------------

@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    stats = environment.stats
    total = stats.total
    print("\n" + "=" * 60)
    print("BOOKSHOP TEST SUMMARY")
    print("=" * 60)
    print(f"  Total requests  : {total.num_requests}")
    print(f"  Failures        : {total.num_failures}")
    print(f"  Avg latency     : {total.avg_response_time:.1f} ms")
    print(f"  95th percentile : {total.get_response_time_percentile(0.95):.1f} ms")
    print(f"  Max latency     : {total.max_response_time:.1f} ms")
    print(f"  Req/s           : {total.current_rps:.2f}")

    # Failover-specific summary
    kill_t    = _failover_state.get("kill_time")
    recover_t = _failover_state.get("recover_time")
    if kill_t:
        recovery_secs = f"{recover_t - kill_t:.1f}s" if recover_t else "not yet recovered"
        print(f"\n  [S5] Leader killed      : {_failover_state.get('leader_endpoint', _DEFAULT_LEADER_CONTAINER)}")
        print(f"  [S5] Recovery time      : {recovery_secs}")

    print("=" * 60)
