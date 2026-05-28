import os
import sys
import time
import random
import threading
import logging
import re

import grpc
from concurrent import futures

os.environ["SERVICE_NAME"] = "order-executor"

FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(FILE), '..', '..')))

from utils import monitoring
from utils.monitoring import (
    tracer,
    orders_counter,
    aborts_counter,
    tx_latency_histo,
    prepare_votes_counter,
    transaction_outcomes_counter,
    bully_elections_started_counter,
)
order_executor_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/order_executor'))
sys.path.insert(0, order_executor_grpc_path)
import order_executor_pb2 as order_executor
import order_executor_pb2_grpc as order_executor_grpc

order_queue_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/order_queue'))
sys.path.insert(0, order_queue_grpc_path)
import order_queue_pb2 as order_queue
import order_queue_pb2_grpc as order_queue_grpc

database_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/books_database'))
sys.path.insert(0, database_grpc_path)
import database_pb2
import database_pb2_grpc

payment_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/payment'))
sys.path.insert(0, payment_grpc_path)
import payment_pb2
import payment_pb2_grpc

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


def parse_executor_nodes(default_executor_id: int, default_port: int):
    """
    Parse executor nodes from env.

    Supported formats:
    - EXECUTOR_NODES="1=executor1:50050,2=executor2:50050"
    - EXECUTOR_PEERS="1=executor1:50050,2=executor2:50050"
    - PEERS="executor2:50050" or "executor1:50050,executor2:50050"

    Defaults to docker service names so executors can resolve each other.
    """
    raw = os.getenv("EXECUTOR_NODES") or os.getenv("EXECUTOR_PEERS") or os.getenv("PEERS") or ""
    nodes = {}

    if raw.strip():
        for part in raw.split(','):
            entry = part.strip()
            if not entry:
                continue

            if '=' in entry:
                id_part, addr_part = entry.split('=', 1)
                try:
                    node_id = int(id_part.strip())
                except ValueError:
                    continue
                addr = addr_part.strip()
                if addr:
                    nodes[node_id] = addr
                continue

            # PEERS format: infer id from docker service name (executor2:50050 -> 2)
            addr = entry
            match = re.match(r'^executor(\d+):\d+$', addr)
            if match:
                nodes[int(match.group(1))] = addr

    # Ensure this executor is represented with docker service name
    nodes[default_executor_id] = f"executor{default_executor_id}:{default_port}"

    # With 2 executors in docker-compose, keep the full cluster view by default.
    if 1 not in nodes:
        nodes[1] = f"executor1:{default_port}"
    if 2 not in nodes:
        nodes[2] = f"executor2:{default_port}"

    return nodes


def parse_db_targets():
    """Parse BooksDatabase targets from env."""
    raw = os.getenv("BOOKS_DB_TARGETS", "database1:50055,database2:50055,database3:50055")
    targets = [addr.strip() for addr in raw.split(',') if addr.strip()]
    return targets or ["database1:50055", "database2:50055", "database3:50055"]


class OrderExecutorService(order_executor_grpc.OrderExecutorServicer):
    def __init__(self):
        self.id = int(os.getenv("EXECUTOR_ID", "1"))
        self.port = int(os.getenv("EXECUTOR_PORT", "50050"))
        self.db_targets = parse_db_targets()
        self.max_db_write_retries = int(os.getenv("MAX_DB_WRITE_RETRIES", "5"))
        self.db_rpc_timeout_sec = float(os.getenv("DB_RPC_TIMEOUT_SEC", "2"))

        # Required state
        self.is_leader = False
        self.leader_id = None

        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._election_in_progress = False

        self.nodes = parse_executor_nodes(self.id, self.port)
        self._refresh_alive_executors_count()
        logger.info(
            f"Executor {self.id} started | nodes={self.nodes} "
            f"| db_targets={self.db_targets} | retries={self.max_db_write_retries}"
        )

    def _refresh_alive_executors_count(self):
        """
        Update the alive executors gauge with a real heartbeat check.
        This avoids reporting a static cluster size when one executor is down.
        """
        alive = 0
        for node_id, addr in self.nodes.items():
            if node_id == self.id:
                alive += 1
                continue
            try:
                with grpc.insecure_channel(addr) as channel:
                    stub = order_executor_grpc.OrderExecutorStub(channel)
                    hb = stub.Heartbeat(order_executor.HeartbeatRequest(executor_id=self.id), timeout=1)
                    if hb.alive:
                        alive += 1
            except Exception:
                continue

        monitoring._alive_executors_count = alive

    def _get_payment_target(self):
        host = os.getenv("PAYMENT_HOST", "payment")
        port = os.getenv("PAYMENT_PORT", "50061")
        return f"{host}:{port}"

    def _normalize_quantity(self, item):
        # Proto3 defaults missing int32 fields to 0, so map missing/invalid to 1.
        if isinstance(item, dict):
            quantity = item.get("quantity", 1)
        else:
            quantity = getattr(item, "quantity", 0)

        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            return 1

        return quantity if quantity > 0 else 1

    def _get_item_name(self, item):
        if isinstance(item, dict):
            return str(item.get("name", "")).strip()
        return str(getattr(item, "name", "")).strip()

    def _get_order_items(self, order):
        if isinstance(order, dict):
            return order.get("items", [])
        return order.items

    def _is_cas_conflict(self, exc):
        """Return True when exc is a gRPC ABORTED error (version mismatch at DB)."""
        return isinstance(exc, grpc.RpcError) and exc.code() == grpc.StatusCode.ABORTED

    def run_2pc(self, transaction_id, db_targets, key, new_value, expected_version, user_name, payment_amount):
        """
        Runs 2PC with all DB replicas and payment as participants.

        Phase 1: Prepare is sent to every DB replica and to payment in parallel.
                 All participants must vote YES; a single NO causes an Abort.
        Phase 2: Commit (or Abort) is sent to every participant in parallel.
                 Each DB replica applies the write locally (no further fan-out).

        Returns True if all participants committed, False otherwise.
        """
        payment_target = self._get_payment_target()
        votes = {}
        vote_lock = threading.Lock()

        # --- AJOUT METRIQUE : On lance le chronomètre ---
        start_time = time.time()

        # --- AJOUT TRACE : Span globale couvrant tout le processus 2PC ---
        # Entrée/sortie gérées manuellement pour englober les deux phases
        # jusqu'au return (un simple `with` refermerait la span immédiatement).
        span_cm = tracer.start_as_current_span("run_2pc_process")
        span = span_cm.__enter__()
        span.set_attribute("transaction.id", transaction_id)

        def prepare_db(target):
            try:
                with grpc.insecure_channel(target) as channel:
                    stub = database_pb2_grpc.BooksDatabaseStub(channel)
                    resp = stub.Prepare(
                        database_pb2.PrepareRequest(
                            transaction_id=transaction_id,
                            key=key,
                            value=new_value,
                            expected_version=expected_version,
                        ),
                        timeout=self.db_rpc_timeout_sec,
                    )
                    with vote_lock:
                        votes[f'db:{target}'] = resp.vote_yes
                    try:
                        prepare_votes_counter.add(1, {"participant": "db", "vote": "yes" if resp.vote_yes else "no"})
                    except Exception:
                        logger.debug("Failed to increment prepare votes counter")
                    logger.info(
                        f"[2PC][{transaction_id}] DB {target} vote: {'YES' if resp.vote_yes else 'NO'}"
                        + (f" ({resp.error})" if not resp.vote_yes and resp.error else "")
                    )
            except Exception as e:
                logger.warning(f"[2PC][{transaction_id}] DB Prepare to {target} failed: {e}")
                with vote_lock:
                    votes[f'db:{target}'] = False
                try:
                    prepare_votes_counter.add(1, {"participant": "db", "vote": "no"})
                except Exception:
                    logger.debug("Failed to increment prepare votes counter")

        def prepare_payment():
            try:
                with grpc.insecure_channel(payment_target) as channel:
                    stub = payment_pb2_grpc.PaymentServiceStub(channel)
                    resp = stub.Prepare(
                        payment_pb2.PrepareRequest(
                            transaction_id=transaction_id,
                            user_name=user_name,
                            amount=payment_amount,
                        ),
                        timeout=5.0,
                    )
                    with vote_lock:
                        votes['payment'] = resp.vote_yes
                    try:
                        prepare_votes_counter.add(1, {"participant": "payment", "vote": "yes" if resp.vote_yes else "no"})
                    except Exception:
                        logger.debug("Failed to increment prepare votes counter")
                    logger.info(f"[2PC][{transaction_id}] Payment vote: {'YES' if resp.vote_yes else 'NO'}")
            except Exception as e:
                logger.warning(f"[2PC][{transaction_id}] Payment Prepare failed: {e}")
                with vote_lock:
                    votes['payment'] = False
                try:
                    prepare_votes_counter.add(1, {"participant": "payment", "vote": "no"})
                except Exception:
                    logger.debug("Failed to increment prepare votes counter")

        # Phase 1 — all DB replicas + payment in parallel
        logger.info(
            f"[2PC][{transaction_id}] Phase 1: Prepare -> db_targets={db_targets}, payment={payment_target}"
        )
        threads = [threading.Thread(target=prepare_db, args=(t,)) for t in db_targets]
        threads.append(threading.Thread(target=prepare_payment))
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        all_db_yes = all(votes.get(f'db:{t}', False) for t in db_targets)
        all_yes = all_db_yes and votes.get('payment', False)

        # Phase 2 — Commit or Abort sent to all participants in parallel
        if all_yes:
            span.add_event("All participants voted YES. Sending COMMIT.")
            logger.info(f"[2PC][{transaction_id}] Phase 2: All voted YES — sending Commit")
            db_reqs = [(t, database_pb2_grpc.BooksDatabaseStub,
                        database_pb2.CommitRequest(transaction_id=transaction_id)) for t in db_targets]
            pay_req = [(payment_target, payment_pb2_grpc.PaymentServiceStub,
                        payment_pb2.CommitRequest(transaction_id=transaction_id))]
        else:
            span.add_event("At least one participant voted NO or timed out. Sending ABORT.")
            logger.info(f"[2PC][{transaction_id}] Phase 2: ABORT — votes={votes}")
            db_reqs = [(t, database_pb2_grpc.BooksDatabaseStub,
                        database_pb2.AbortRequest(transaction_id=transaction_id)) for t in db_targets]
            pay_req = [(payment_target, payment_pb2_grpc.PaymentServiceStub,
                        payment_pb2.AbortRequest(transaction_id=transaction_id))]

        participants = db_reqs + pay_req

        def send_phase2(target, stub_class, req):
            try:
                with grpc.insecure_channel(target) as channel:
                    if all_yes:
                        stub_class(channel).Commit(req, timeout=5.0)
                    else:
                        stub_class(channel).Abort(req, timeout=5.0)
                logger.info(f"[2PC][{transaction_id}] {'Commit' if all_yes else 'Abort'} sent to {target}")
            except grpc.RpcError as e:
                if self._is_cas_conflict(e):
                    logger.warning(
                        f"[2PC][{transaction_id}] CAS conflict (ABORTED) at {target} during Commit"
                    )
                else:
                    logger.warning(f"[2PC][{transaction_id}] Phase 2 to {target} failed: {e}")
            except Exception as e:
                logger.warning(f"[2PC][{transaction_id}] Phase 2 to {target} failed: {e}")

        threads = [threading.Thread(target=send_phase2, args=(t, s, r)) for t, s, r in participants]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        # --- AJOUT METRIQUE : Enregistrement des métriques selon le résultat ---
        if all_yes:
            duration_ms = (time.time() - start_time) * 1000
            try:
                tx_latency_histo.record(duration_ms, {"outcome": "commit"})
            except Exception:
                logger.debug("Failed to record tx latency metric")
            try:
                transaction_outcomes_counter.add(1, {"outcome": "commit"})
            except Exception:
                logger.debug("Failed to increment transaction outcomes counter")
        else:
            try:
                aborts_counter.add(1, {"reason": "vote_failed"})
            except Exception:
                logger.debug("Failed to increment aborts counter")
            try:
                transaction_outcomes_counter.add(1, {"outcome": "abort"})
            except Exception:
                logger.debug("Failed to increment transaction outcomes counter")

        # Close the 2PC span now that both phases (and metrics) are recorded.
        span_cm.__exit__(None, None, None)

        return all_yes

    def execute_order(self, order):
        items = self._get_order_items(order)
        order_id = getattr(order, 'order_id', 'unknown')
        user_name = getattr(order, 'user_name', '')
        orders_counter.add(1, {"status": "received"})

        for item in items:
            item_name = self._get_item_name(item)
            quantity = self._normalize_quantity(item)

            if not item_name:
                logger.warning("Order item with empty name encountered")
                return False

            for attempt in range(1, self.max_db_write_retries + 1):
                # Use a random replica for the quorum-read entry point.
                read_target = random.choice(self.db_targets)

                # Read current stock (quorum read handled inside the DB service)
                try:
                    with grpc.insecure_channel(read_target) as channel:
                        db_stub = database_pb2_grpc.BooksDatabaseStub(channel)
                        read_resp = db_stub.Read(
                            database_pb2.ReadRequest(key=item_name),
                            timeout=self.db_rpc_timeout_sec,
                        )
                except Exception as exc:
                    logger.warning(
                        f"DB Read failed for item={item_name} target={read_target} "
                        f"attempt={attempt}/{self.max_db_write_retries}: {exc}"
                    )
                    time.sleep(random.uniform(0.05, 0.2))
                    continue

                if not read_resp.found:
                    logger.warning(f"Book not found in DB: {item_name}")
                    return False

                try:
                    current_stock = int(read_resp.value)
                except ValueError:
                    logger.warning(f"Invalid stock value for {item_name}: {read_resp.value}")
                    return False

                if current_stock < quantity:
                    logger.warning(
                        f"Insufficient stock for {item_name}: requested={quantity}, available={current_stock}"
                    )
                    return False

                new_stock = current_stock - quantity
                transaction_id = f"{order_id}-{item_name}-{attempt}"

                success = self.run_2pc(
                    transaction_id=transaction_id,
                    db_targets=self.db_targets,
                    key=item_name,
                    new_value=str(new_stock),
                    expected_version=read_resp.version,
                    user_name=user_name,
                    payment_amount=float(quantity),
                )

                if success:
                    logger.info(
                        f"[{order_id}] Stock committed: {item_name} {current_stock} -> {new_stock}"
                    )
                    break

                # Could be CAS conflict or DB vote NO — retry with exponential backoff + jitter.
                # Known limitation: processing is sequential (dequeue → full 2PC → next dequeue),
                # so a stuck retry loop here blocks the entire queue.
                logger.info(f"[{order_id}] 2PC failed for {item_name} attempt={attempt}/{self.max_db_write_retries}, retrying")
                time.sleep(random.uniform(0.1, 0.5) * attempt)
            else:
                logger.warning(f"[{order_id}] Failed to commit {item_name} after {self.max_db_write_retries} attempts")
                return False

        return True

    def Election(self, request, context):
        """
        Bully election handler.
        If requester has lower id, acknowledge and trigger own election.
        """
        requester_id = request.executor_id

        if requester_id < self.id:
            logger.info(f"Received Election from lower executor {requester_id}; replying OK and starting local election")
            threading.Thread(target=self.start_election, kwargs={"trigger": "peer_request"}, daemon=True).start()
            return order_executor.ElectionResponse(executor_id=self.id, ok=True)

        logger.info(f"Received Election from executor {requester_id}; no takeover (self={self.id})")
        return order_executor.ElectionResponse(executor_id=self.id, ok=False)

    def Coordinator(self, request, context):
        """Receive leader announcement."""
        with self._state_lock:
            self.leader_id = request.executor_id
            self.is_leader = (self.id == request.executor_id)
            self._election_in_progress = False
            monitoring._current_leader_id = self.leader_id or 0

        logger.info(f"Coordinator received: leader={self.leader_id} | self.is_leader={self.is_leader}")
        return order_executor.CoordinatorResponse(executor_id=self.id, acknowledged=True)

    def Heartbeat(self, request, context):
        """Simple liveness response."""
        return order_executor.HeartbeatResponse(executor_id=self.id, alive=True)

    def start_election(self, trigger="unknown"):
        with self._state_lock:
            if self._election_in_progress:
                return
            self._election_in_progress = True

        bully_elections_started_counter.add(1, {"trigger": trigger})
        logger.info(f"Executor {self.id}: starting Bully election")

        got_ok_from_higher = False
        higher_nodes = {node_id: addr for node_id, addr in self.nodes.items() if node_id > self.id}

        # For the 2-node setup, executor1 must always try executor2 before self-election.
        if self.id == 1 and 2 in self.nodes and 2 not in higher_nodes:
            higher_nodes[2] = self.nodes[2]

        for node_id, addr in sorted(higher_nodes.items()):
            try:
                logger.info(f"Executor {self.id}: sending Election to executor {node_id} at {addr}")
                with grpc.insecure_channel(addr) as channel:
                    stub = order_executor_grpc.OrderExecutorStub(channel)
                    response = stub.Election(order_executor.ElectionRequest(executor_id=self.id), timeout=2)
                    if response.ok:
                        got_ok_from_higher = True
                        logger.info(f"Executor {self.id}: higher executor {node_id} responded OK")
            except Exception:
                logger.warning(f"Executor {self.id}: higher executor {node_id} unreachable during election")

        if got_ok_from_higher:
            logger.info(f"Executor {self.id}: waiting for coordinator announcement")
            return

        # No higher executor responded: become leader
        with self._state_lock:
            self.leader_id = self.id
            self.is_leader = True
            self._election_in_progress = False
            monitoring._current_leader_id = self.id

        logger.info(f"Executor {self.id}: elected as leader")
        self.broadcast_coordinator()

    def broadcast_coordinator(self):
        for node_id, addr in self.nodes.items():
            if node_id == self.id:
                continue
            try:
                with grpc.insecure_channel(addr) as channel:
                    stub = order_executor_grpc.OrderExecutorStub(channel)
                    stub.Coordinator(order_executor.CoordinatorRequest(executor_id=self.id), timeout=2)
                logger.info(f"Executor {self.id}: coordinator announcement sent to {node_id}")
            except Exception:
                logger.warning(f"Executor {self.id}: could not announce coordinator to {node_id}")

    def leader_execution_loop(self):
        """
        If this executor is leader, continuously dequeue orders from order_queue.
        """
        while not self._stop_event.is_set():
            self._refresh_alive_executors_count()

            with self._state_lock:
                am_leader = self.is_leader

            if not am_leader:
                time.sleep(1)
                continue

            try:
                with grpc.insecure_channel('order_queue:50054') as channel:
                    queue_stub = order_queue_grpc.OrderQueueStub(channel)
                    resp = queue_stub.Dequeue(
                        order_queue.DequeueRequest(caller_id=f"executor{self.id}"), timeout=2
                    )

                if resp.success:
                    order = resp.order
                    logger.info(f"[{order.order_id}] Executing order | user: {order.user_name} | items: {[i.name for i in order.items]}")
                    if self.execute_order(order):
                        logger.info(f"[{order.order_id}] Order executed successfully")
                    else:
                        logger.warning(f"[{order.order_id}] Order execution failed")
                else:
                    time.sleep(2)
            except Exception as ex:
                logger.warning(f"Leader dequeue failed: {ex}")
                time.sleep(2)

    def follower_heartbeat_loop(self):
        """
        Every 5 seconds, if not leader, ping current leader.
        If ping fails (or no leader known), trigger election.
        """
        while not self._stop_event.is_set():
            time.sleep(5)
            self._refresh_alive_executors_count()

            with self._state_lock:
                am_leader = self.is_leader
                current_leader = self.leader_id

            if am_leader:
                continue

            if current_leader is None:
                logger.info(f"Executor {self.id}: no known leader, triggering election")
                self.start_election(trigger="no_leader")
                continue

            leader_addr = self.nodes.get(current_leader)
            if not leader_addr:
                logger.warning(f"Executor {self.id}: leader address missing for {current_leader}, triggering election")
                self.start_election(trigger="leader_unknown")
                continue

            try:
                with grpc.insecure_channel(leader_addr) as channel:
                    stub = order_executor_grpc.OrderExecutorStub(channel)
                    hb = stub.Heartbeat(order_executor.HeartbeatRequest(executor_id=self.id), timeout=2)

                if not hb.alive:
                    logger.warning(f"Executor {self.id}: leader {current_leader} not alive, triggering election")
                    self.start_election(trigger="leader_dead")
            except Exception:
                logger.warning(f"Executor {self.id}: heartbeat to leader {current_leader} failed, triggering election")
                self.start_election(trigger="heartbeat_timeout")


def serve():
    service = OrderExecutorService()

    threading.Thread(target=service.leader_execution_loop, daemon=True).start()
    threading.Thread(target=service.follower_heartbeat_loop, daemon=True).start()

    # Bootstrap election at startup so a leader emerges.
    # Delay allows all peer containers to come online before Election messages are sent.
    def bootstrap():
        time.sleep(3)
        service.start_election(trigger="startup")

    threading.Thread(target=bootstrap, daemon=True).start()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    order_executor_grpc.add_OrderExecutorServicer_to_server(service, server)
    server.add_insecure_port(f"[::]:{service.port}")
    logger.info(f"Order Executor service started on port {service.port} | executor_id={service.id}")
    server.start()
    server.wait_for_termination()


if __name__ == '__main__':
    serve()