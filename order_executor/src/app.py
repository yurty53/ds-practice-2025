import os
import sys
import time
import random
import threading
import logging
import re

import grpc
from concurrent import futures

FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
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
        logger.info(
            f"Executor {self.id} started | nodes={self.nodes} "
            f"| db_targets={self.db_targets} | retries={self.max_db_write_retries}"
        )

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

    def _is_cas_conflict(self, error_text):
        return "version mismatch" in (error_text or "").lower()

    def execute_order(self, order):
        items = self._get_order_items(order)

        for item in items:
            item_name = self._get_item_name(item)
            quantity = self._normalize_quantity(item)

            if not item_name:
                logger.warning("Order item with empty name encountered")
                return False

            for attempt in range(1, self.max_db_write_retries + 1):
                db_target = random.choice(self.db_targets)

                try:
                    with grpc.insecure_channel(db_target) as channel:
                        db_stub = database_pb2_grpc.BooksDatabaseStub(channel)
                        read_resp = db_stub.Read(
                            database_pb2.ReadRequest(key=item_name),
                            timeout=self.db_rpc_timeout_sec,
                        )
                except Exception as exc:
                    logger.warning(
                        f"DB Read failed for item={item_name} target={db_target} "
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

                try:
                    with grpc.insecure_channel(db_target) as channel:
                        db_stub = database_pb2_grpc.BooksDatabaseStub(channel)
                        write_resp = db_stub.Write(
                            database_pb2.WriteRequest(
                                key=item_name,
                                value=str(new_stock),
                                expected_version=read_resp.version,
                            ),
                            timeout=self.db_rpc_timeout_sec,
                        )
                except Exception as exc:
                    logger.warning(
                        f"DB Write failed for item={item_name} target={db_target} "
                        f"attempt={attempt}/{self.max_db_write_retries}: {exc}"
                    )
                    time.sleep(random.uniform(0.05, 0.2))
                    continue

                if write_resp.success:
                    logger.info(
                        f"Stock updated for {item_name}: {current_stock} -> {new_stock} "
                        f"(version={write_resp.version})"
                    )
                    break

                if self._is_cas_conflict(write_resp.error):
                    logger.info(
                        f"CAS conflict for {item_name} on attempt {attempt}/{self.max_db_write_retries}; retrying"
                    )
                    time.sleep(random.uniform(0.05, 0.2))
                    continue

                logger.warning(f"Write rejected for {item_name}: {write_resp.error}")
                return False
            else:
                logger.warning(f"Failed to update stock for {item_name} after retries")
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
            threading.Thread(target=self.start_election, daemon=True).start()
            return order_executor.ElectionResponse(executor_id=self.id, ok=True)

        logger.info(f"Received Election from executor {requester_id}; no takeover (self={self.id})")
        return order_executor.ElectionResponse(executor_id=self.id, ok=False)

    def Coordinator(self, request, context):
        """Receive leader announcement."""
        with self._state_lock:
            self.leader_id = request.executor_id
            self.is_leader = (self.id == request.executor_id)
            self._election_in_progress = False

        logger.info(f"Coordinator received: leader={self.leader_id} | self.is_leader={self.is_leader}")
        return order_executor.CoordinatorResponse(executor_id=self.id, acknowledged=True)

    def Heartbeat(self, request, context):
        """Simple liveness response."""
        return order_executor.HeartbeatResponse(executor_id=self.id, alive=True)

    def start_election(self):
        with self._state_lock:
            if self._election_in_progress:
                return
            self._election_in_progress = True

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
            with self._state_lock:
                am_leader = self.is_leader

            if not am_leader:
                time.sleep(1)
                continue

            try:
                with grpc.insecure_channel('order_queue:50054') as channel:
                    queue_stub = order_queue_grpc.OrderQueueStub(channel)
                    resp = queue_stub.Dequeue(order_queue.DequeueRequest(), timeout=2)

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

            with self._state_lock:
                am_leader = self.is_leader
                current_leader = self.leader_id

            if am_leader:
                continue

            if current_leader is None:
                logger.info(f"Executor {self.id}: no known leader, triggering election")
                self.start_election()
                continue

            leader_addr = self.nodes.get(current_leader)
            if not leader_addr:
                logger.warning(f"Executor {self.id}: leader address missing for {current_leader}, triggering election")
                self.start_election()
                continue

            try:
                with grpc.insecure_channel(leader_addr) as channel:
                    stub = order_executor_grpc.OrderExecutorStub(channel)
                    hb = stub.Heartbeat(order_executor.HeartbeatRequest(executor_id=self.id), timeout=2)

                if not hb.alive:
                    logger.warning(f"Executor {self.id}: leader {current_leader} not alive, triggering election")
                    self.start_election()
            except Exception:
                logger.warning(f"Executor {self.id}: heartbeat to leader {current_leader} failed, triggering election")
                self.start_election()


def serve():
    service = OrderExecutorService()

    threading.Thread(target=service.leader_execution_loop, daemon=True).start()
    threading.Thread(target=service.follower_heartbeat_loop, daemon=True).start()

    # Bootstrap election at startup so a leader emerges.
    # Delay allows all peer containers to come online before Election messages are sent.
    def bootstrap():
        time.sleep(3)
        service.start_election()

    threading.Thread(target=bootstrap, daemon=True).start()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    order_executor_grpc.add_OrderExecutorServicer_to_server(service, server)
    server.add_insecure_port(f"[::]:{service.port}")
    logger.info(f"Order Executor service started on port {service.port} | executor_id={service.id}")
    server.start()
    server.wait_for_termination()


if __name__ == '__main__':
    serve()
