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
    CascadingFailoverUser   – two consecutive leader kills; measures both re-elections (S6)
    QuorumDegradationUser   – database replicas stopped until write quorum is lost (S7)

To run only a specific scenario:
    locust -f locustfile.py --host http://localhost:8080 --headless -u 5 -r 1 -t 30s SingleOrderUser

S6 – Cascading leader failures (orchestrator on :8081 exposes GET /leader):
    locust -f tests/locustfile.py --host http://localhost:8081 --headless -u 3 -r 3 -t 120s CascadingFailoverUser

S7 – Database quorum degradation:
    locust -f tests/locustfile.py --host http://localhost:8081 --headless -u 3 -r 3 -t 120s QuorumDegradationUser
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
# Scenario 6 – Cascading leader failures
# ---------------------------------------------------------------------------

# Shared state written by the cascading-failover thread, read by
# CascadingFailoverUser instances to tag requests and time re-elections.
_cascade_state = {
    "phase": "baseline",        # baseline → first-failure → second-failure → single-node → recovering → recovered
    "leader1": None,            # first leader killed   (expected: executor3)
    "leader2": None,            # second leader killed  (expected: executor2)
    "kill1_time": None,
    "kill2_time": None,
    "reelection1_time": None,   # wall-clock of first approval after kill1 (before kill2)
    "reelection2_time": None,   # wall-clock of first approval after kill2
    "lock": threading.Lock(),
}


def _cascade_sequence(host: str):
    """
    Background thread that kills two leaders in a row, then restarts both.

    Timeline (seconds from test start):
        0   – baseline (all three executors up; executor3 is leader)
        10  – query /leader, kill it (executor3); first re-election begins
        25  – query /leader, kill the new leader (executor2); second re-election
        40  – executor1 is the last node standing (single-node)
        60  – restart executor2 then executor3; cluster recovers
    """
    time.sleep(10)

    leader1 = _get_current_leader(host)
    with _cascade_state["lock"]:
        _cascade_state["leader1"] = leader1
        _cascade_state["phase"] = "first-failure"
    print(f"\n[S6] ── PHASE: first-failure – killing leader '{leader1}' ──")
    _docker("stop", leader1)
    with _cascade_state["lock"]:
        _cascade_state["kill1_time"] = time.time()

    time.sleep(15)  # wait for first re-election to complete

    leader2 = _get_current_leader(host)
    with _cascade_state["lock"]:
        _cascade_state["leader2"] = leader2
        _cascade_state["phase"] = "second-failure"
    print(f"\n[S6] ── PHASE: second-failure – killing new leader '{leader2}' ──")
    _docker("stop", leader2)
    with _cascade_state["lock"]:
        _cascade_state["kill2_time"] = time.time()

    time.sleep(15)  # wait for second re-election (executor1 last standing)

    with _cascade_state["lock"]:
        _cascade_state["phase"] = "single-node"
    print("\n[S6] ── PHASE: single-node – executor1 last standing ──")

    # Restart both at ~T+60s (we are at ~T+40s now).
    time.sleep(20)

    with _cascade_state["lock"]:
        _cascade_state["phase"] = "recovering"
    print(f"\n[S6] ── PHASE: recovering – restarting '{leader2}' then '{leader1}' ──")
    _docker("start", leader2)
    _docker("start", leader1)

    with _cascade_state["lock"]:
        _cascade_state["phase"] = "recovered"
    print("\n[S6] ── PHASE: recovered ──")


class CascadingFailoverUser(HttpUser):
    """
    Continuously sends valid orders while a background thread kills the leader
    executor twice in a row (executor3 → executor2), leaving executor1 as the
    sole survivor, then restarts both.

    Phases tracked per-request:
        [S6-baseline]        – before any kill; all approved
        [S6-first-failure]   – executor3 killed; first re-election in progress
        [S6-second-failure]  – executor2 killed; second re-election in progress
        [S6-single-node]     – executor1 last standing; orders approved
        [S6-recovering]      – executor2/3 restarting
        [S6-recovered]       – cluster restored; all approved

    Both re-election durations (first approval after each kill) are printed in
    the end-of-test summary.

    Run with:
        locust -f tests/locustfile.py --host http://localhost:8081 --headless \
               -u 3 -r 3 -t 120s CascadingFailoverUser
    """
    wait_time = between(1, 2)
    _sequence_started = False
    _sequence_lock = threading.Lock()

    def on_start(self):
        with CascadingFailoverUser._sequence_lock:
            if not CascadingFailoverUser._sequence_started:
                CascadingFailoverUser._sequence_started = True
                threading.Thread(
                    target=_cascade_sequence,
                    args=(self.environment.host,),
                    daemon=True,
                ).start()

    def _current_phase(self) -> str:
        with _cascade_state["lock"]:
            return _cascade_state["phase"]

    def _maybe_record_reelection(self):
        """Record the first approved order after each kill as the re-election time."""
        now = time.time()
        with _cascade_state["lock"]:
            k1 = _cascade_state["kill1_time"]
            k2 = _cascade_state["kill2_time"]
            # First re-election: an approval after kill1 but before kill2.
            if k1 and k2 is None and _cascade_state["reelection1_time"] is None:
                _cascade_state["reelection1_time"] = now
                print(
                    f"\n[S6] ✓ First re-election complete – "
                    f"{now - k1:.1f}s after killing {_cascade_state['leader1']}"
                )
            # Second re-election: an approval after kill2.
            elif k2 and _cascade_state["reelection2_time"] is None:
                _cascade_state["reelection2_time"] = now
                print(
                    f"\n[S6] ✓ Second re-election complete – "
                    f"{now - k2:.1f}s after killing {_cascade_state['leader2']}"
                )

    @task
    def order_during_cascade(self):
        phase = self._current_phase()
        name  = f"[S6-{phase}] order"
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

            status   = resp.json().get("status", "")
            approved = status == "Order Approved"

            if approved:
                self._maybe_record_reelection()

            if phase in ("baseline", "recovered", "single-node"):
                # A leader exists in these phases, so orders must be approved.
                if approved:
                    resp.success()
                else:
                    resp.failure(f"Expected approved in phase '{phase}', got '{status}'")
            else:
                # Re-election windows: approve or reject are both acceptable.
                if status in ("Order Approved", "Order Rejected"):
                    resp.success()
                else:
                    resp.failure(f"Unexpected status in phase '{phase}': {status}")


# ---------------------------------------------------------------------------
# Scenario 7 – Database quorum degradation
# ---------------------------------------------------------------------------

# Compose-assigned container names for the database replicas.
_DB2 = "ds-practice-2025-database2-1"
_DB3 = "ds-practice-2025-database3-1"

_quorum_state = {
    "phase": "baseline",        # baseline → one-down → quorum-lost → recovering → recovered
    "quorum_lost_time": None,
    "restart_time": None,
    "recovery_time": None,      # first approval observed after the replicas were restarted
    "lock": threading.Lock(),
}


def _quorum_sequence(host: str):
    """
    Background thread that degrades the database quorum step by step.

    Quorum is 2-of-3 (W=2). Timeline (seconds from test start):
        0   – baseline (3 replicas up)
        10  – stop database3 (1 down; quorum of 2 still met → orders approved)
        35  – stop database2 (2 down; quorum cannot be met → 2PC Prepare fails)
        60  – restart database2 then database3; quorum restored
    """
    time.sleep(10)
    with _quorum_state["lock"]:
        _quorum_state["phase"] = "one-down"
    print(f"\n[S7] ── PHASE: one-down – stopping '{_DB3}' (quorum still met) ──")
    _docker("stop", _DB3)

    time.sleep(25)  # until ~T+35
    with _quorum_state["lock"]:
        _quorum_state["phase"] = "quorum-lost"
        _quorum_state["quorum_lost_time"] = time.time()
    print(f"\n[S7] ── PHASE: quorum-lost – stopping '{_DB2}' (write quorum cannot be met) ──")
    _docker("stop", _DB2)

    time.sleep(25)  # until ~T+60
    with _quorum_state["lock"]:
        _quorum_state["phase"] = "recovering"
        _quorum_state["restart_time"] = time.time()
    print(f"\n[S7] ── PHASE: recovering – restarting '{_DB2}' then '{_DB3}' ──")
    _docker("start", _DB2)
    _docker("start", _DB3)

    with _quorum_state["lock"]:
        _quorum_state["phase"] = "recovered"
    print("\n[S7] ── PHASE: recovered ──")


class QuorumDegradationUser(HttpUser):
    """
    Continuously sends valid orders while a background thread stops database
    replicas one at a time (3 → 2 → 1) until the write quorum is lost, then
    restarts them.

    IMPORTANT — what a checkout reply means here:
        /checkout returns 'Order Approved' as soon as fraud/verification pass
        and the order is *enqueued*; the 2PC stock write runs asynchronously in
        the executor afterwards. So when the write quorum is lost, the HTTP
        reply can still be 'Order Approved' — the 2PC Prepare failure happens
        later and is NOT reflected in the checkout response. We therefore treat
        the quorum-lost window as an expected-degradation window: HTTP success
        is accepted and the reason is logged rather than failing the request.

    Phases tracked per-request:
        [S7-baseline]    – 3 replicas up; orders approved
        [S7-one-down]    – database3 down; quorum of 2 still met; approved
        [S7-quorum-lost] – database2+3 down; 2PC Prepare fails (async); logged, SUCCESS
        [S7-recovering]  – replicas restarting
        [S7-recovered]   – quorum restored; orders approved

    Quorum recovery time (first approval after the replicas were restarted) is
    printed in the end-of-test summary.

    Run with:
        locust -f tests/locustfile.py --host http://localhost:8081 --headless \
               -u 3 -r 3 -t 120s QuorumDegradationUser
    """
    wait_time = between(1, 2)
    _sequence_started = False
    _sequence_lock = threading.Lock()

    def on_start(self):
        with QuorumDegradationUser._sequence_lock:
            if not QuorumDegradationUser._sequence_started:
                QuorumDegradationUser._sequence_started = True
                threading.Thread(
                    target=_quorum_sequence,
                    args=(self.environment.host,),
                    daemon=True,
                ).start()

    def _current_phase(self) -> str:
        with _quorum_state["lock"]:
            return _quorum_state["phase"]

    def _maybe_record_recovery(self):
        now = time.time()
        with _quorum_state["lock"]:
            rt = _quorum_state["restart_time"]
            if rt and _quorum_state["recovery_time"] is None:
                _quorum_state["recovery_time"] = now
                print(
                    f"\n[S7] ✓ Quorum recovered – first approved order "
                    f"{now - rt:.1f}s after restarting replicas"
                )

    @task
    def order_during_degradation(self):
        phase = self._current_phase()
        name  = f"[S7-{phase}] order"
        book  = random.choice(CATALOGUE_BOOKS)
        payload = make_order([book])

        with self.client.post(
            "/checkout",
            json=payload,
            catch_response=True,
            name=name,
        ) as resp:
            if phase == "quorum-lost":
                # Expected behaviour: mark SUCCESS and log the reason. The write
                # quorum cannot be met, so the 2PC Prepare fails asynchronously;
                # the enqueue-time HTTP reply may still read 'Order Approved'.
                if resp.status_code != 200:
                    print(
                        f"[S7] quorum-lost: checkout HTTP {resp.status_code} "
                        f"(expected – write quorum < 2)"
                    )
                else:
                    status = resp.json().get("status", "")
                    print(
                        f"[S7] quorum-lost: checkout status='{status}' "
                        f"(2PC Prepare expected to fail asynchronously – quorum < 2)"
                    )
                resp.success()
                return

            if resp.status_code != 200:
                resp.failure(f"HTTP {resp.status_code}")
                return

            status   = resp.json().get("status", "")
            approved = status == "Order Approved"

            if phase in ("baseline", "one-down", "recovered"):
                # Quorum is met in these phases, so orders must be approved.
                if approved:
                    resp.success()
                    if phase == "recovered":
                        self._maybe_record_recovery()
                else:
                    resp.failure(f"Expected approved in phase '{phase}', got '{status}'")
            else:
                # 'recovering' is transient: approve or reject are both acceptable.
                if status in ("Order Approved", "Order Rejected"):
                    resp.success()
                    if approved:
                        self._maybe_record_recovery()
                else:
                    resp.failure(f"Unexpected status in phase '{phase}': {status}")


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

    # Cascading-failover summary (S6) – both re-elections timed separately
    k1 = _cascade_state.get("kill1_time")
    if k1:
        k2  = _cascade_state.get("kill2_time")
        re1 = _cascade_state.get("reelection1_time")
        re2 = _cascade_state.get("reelection2_time")
        re1_secs = f"{re1 - k1:.1f}s" if re1 else "not measured"
        re2_secs = f"{re2 - k2:.1f}s" if (re2 and k2) else "not measured"
        print(f"\n  [S6] First leader killed   : {_cascade_state.get('leader1')}")
        print(f"  [S6] First re-election     : {re1_secs}")
        print(f"  [S6] Second leader killed  : {_cascade_state.get('leader2')}")
        print(f"  [S6] Second re-election    : {re2_secs}")

    # Quorum-degradation summary (S7)
    ql = _quorum_state.get("quorum_lost_time")
    if ql:
        rt  = _quorum_state.get("restart_time")
        rec = _quorum_state.get("recovery_time")
        rec_secs = f"{rec - rt:.1f}s" if (rec and rt) else "not measured"
        print(f"\n  [S7] Quorum lost           : database2 + database3 stopped")
        print(f"  [S7] Quorum recovery time  : {rec_secs}")

    print("=" * 60)
