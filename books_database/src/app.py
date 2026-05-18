import sys
import os
import json
import grpc
import logging
import threading
from concurrent import futures

FILE = __file__ if '__file__' in dir() else os.getenv('PYTHONFILE', '')
sys.path.insert(0, os.path.join(os.path.dirname(FILE), '../../utils/pb/books_database'))

import database_pb2
import database_pb2_grpc
from kv_store import KVStore
from quorum import quorum_read, quorum_write

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

DEFAULT_CATALOGUE = {
    "Magical Realism": [
        {"title": "100 Years of Solitude", "author": "Gabriel García Márquez"},
        {"title": "Feast of the Goat", "author": "Mario Vargas Llosa"},
        {"title": "The House of Spirits", "author": "Isabel Allende"},
    ],
    "Classic Literature": [
        {"title": "Brothers Karamazov", "author": "Fyodor Dostoevsky"},
        {"title": "Les Misérables", "author": "Victor Hugo"},
        {"title": "The Iliad", "author": "Homer"},
    ],
    "Fantasy": [
        {"title": "Lord of the Rings", "author": "J.R.R. Tolkien"},
        {"title": "The Name of the Wind", "author": "Patrick Rothfuss"},
        {"title": "The Chronicles of Narnia", "author": "C.S. Lewis"},
    ],
    "Sci-Fi": [
        {"title": "Dune", "author": "Frank Herbert"},
        {"title": "Neuromancer", "author": "William Gibson"},
        {"title": "Foundation", "author": "Isaac Asimov"},
    ],
    "Literary Fiction": [
        {"title": "Midnight's Children", "author": "Salman Rushdie"},
        {"title": "God of Small Things", "author": "Arundhati Roy"},
        {"title": "The Grapes of Wrath", "author": "John Steinbeck"},
    ],
}


def seed_initial_catalogue(store):
    """Populate the replica with the initial catalogue and stock values."""
    store.local_write("catalogue", json.dumps(DEFAULT_CATALOGUE), 0)

    for books in DEFAULT_CATALOGUE.values():
        for book in books:
            store.local_write(book["title"], "10", 0)

# Parse peer addresses from environment variable
# Format: "database2:50055,database3:50055"
def parse_peers():
    raw = os.getenv('PEERS', '')
    if not raw.strip():
        return []
    return [addr.strip() for addr in raw.split(',') if addr.strip()]


class BooksDatabaseServicer(database_pb2_grpc.BooksDatabaseServicer):
    def __init__(self, peer_addresses):
        self.store = KVStore()
        seed_initial_catalogue(self.store)
        self.peers = peer_addresses
        node_id = os.getenv('NODE_ID', 'unknown')
        total_books = sum(len(books) for books in DEFAULT_CATALOGUE.values())
        logger.info(f"[{node_id}] Books Database started | peers={self.peers} | seeded_catalogue={total_books} books")

        # ── 2PC state ─────────────────────────────────────────────────────
        # staged_writes: { transaction_id → {key, value, expected_version} }
        self._staged_writes = {}
 
        # locks: { key → transaction_id }
        # Prevents two concurrent transactions from preparing a write to the
        # same key at the same time.
        self._locks = {}
 
        # Single mutex protecting both dicts
        self._2pc_lock = threading.Lock()
 
        total_books = sum(len(books) for books in DEFAULT_CATALOGUE.values())
        logger.info(
            f"[{node_id}] Books Database started | peers={self.peers} "
            f"| seeded_catalogue={total_books} books"
        )

    # ── Local handlers (called by peer quorum functions) ──────────────────

    def LocalRead(self, request, context):
        value, version = self.store.local_read(request.key)
        return database_pb2.ReadResponse(
            value=value or "",
            version=version,
            found=value is not None
        )

    def LocalWrite(self, request, context):
        success, version = self.store.local_write(
            request.key,
            request.value,
            request.expected_version
        )
        return database_pb2.WriteResponse(
            success=success,
            version=version,
            error="" if success else "Version mismatch"
        )

    # ── Quorum handlers (called by external clients) ───────────────────────

    def Read(self, request, context):
        """
        Quorum read: contact self + peers, return value with highest version.
        Requires QUORUM_SIZE=2 responses out of 3 replicas.
        """
        try:
            value, version, found = quorum_read(
                key=request.key,
                local_store=self.store,
                peer_addresses=self.peers,
            )
            return database_pb2.ReadResponse(
                value=value,
                version=version,
                found=found,
            )
        except RuntimeError as exc:
            logger.error(f"[Read] quorum failed for key={request.key}: {exc}")
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details(str(exc))
            return database_pb2.ReadResponse(value="", version=0, found=False)

    def Write(self, request, context):
        """
        Quorum write: write to self + peers, require QUORUM_SIZE=2 ACKs.
        Uses optimistic concurrency — caller must pass the version obtained
        from a prior Read call as expected_version.
        """
        try:
            success, version, error = quorum_write(
                key=request.key,
                value=request.value,
                expected_version=request.expected_version,
                local_store=self.store,
                peer_addresses=self.peers,
            )
            return database_pb2.WriteResponse(
                success=success,
                version=version,
                error=error,
            )
        except Exception as exc:
            logger.error(f"[Write] unexpected error for key={request.key}: {exc}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(exc))
            return database_pb2.WriteResponse(success=False, version=0, error=str(exc))
        
        
    # ── 2PC handlers ───────────────────────────────────────────────────────
 
    def Prepare(self, request, context):
        """
        Phase 1 of 2PC — stage the write without applying it.

        VOTE_NO  if the key is already locked by another in-progress transaction
                 or if expected_version does not match the current stored version (CAS guard).
        VOTE_YES otherwise: stage the write in _staged_writes and lock the key.
        Nothing touches the real KV store yet.
        """
        tid     = request.transaction_id
        key     = request.key
        node_id = os.getenv('NODE_ID', 'unknown')

        with self._2pc_lock:
            existing_tid = self._locks.get(key)
            if existing_tid is not None and existing_tid != tid:
                logger.warning(
                    f"[{node_id}] [2PC] Prepare VOTE_NO | tid={tid} "
                    f"key='{key}' already locked by tid={existing_tid}"
                )
                return database_pb2.PrepareResponse(
                    vote_yes=False,
                    error=f"Key '{key}' is locked by transaction {existing_tid}"
                )

            # CAS guard: reject if the caller's expected version is stale.
            _, current_version = self.store.local_read(key)
            if current_version != request.expected_version:
                logger.warning(
                    f"[{node_id}] [2PC] Prepare VOTE_NO | tid={tid} key='{key}' "
                    f"version mismatch: expected={request.expected_version} current={current_version}"
                )
                return database_pb2.PrepareResponse(
                    vote_yes=False,
                    error=f"Version mismatch: expected {request.expected_version}, got {current_version}"
                )

            self._staged_writes[tid] = {
                "key":              key,
                "value":            request.value,
                "expected_version": request.expected_version,
            }
            self._locks[key] = tid

        logger.info(
            f"[{node_id}] [2PC] Prepare VOTE_YES | tid={tid} "
            f"key='{key}' expected_version={request.expected_version}"
        )
        return database_pb2.PrepareResponse(vote_yes=True, error="")
 
    def Commit(self, request, context):
        """
        Phase 2 of 2PC — happy path.

        All replicas received Prepare, so each applies the staged write locally
        via local_write (no further fan-out needed). Release the lock and clean
        up staging afterwards. Sets gRPC status ABORTED on a version mismatch so
        the coordinator can distinguish a CAS conflict from other failures.
        """
        tid     = request.transaction_id
        node_id = os.getenv('NODE_ID', 'unknown')

        with self._2pc_lock:
            staged = self._staged_writes.get(tid)

        if staged is None:
            logger.warning(
                f"[{node_id}] [2PC] Commit called but no staged write for tid={tid}"
            )
            return database_pb2.CommitResponse(success=False)

        key              = staged["key"]
        value            = staged["value"]
        expected_version = staged["expected_version"]

        logger.info(
            f"[{node_id}] [2PC] Commit | tid={tid} "
            f"key='{key}' expected_version={expected_version}"
        )

        # Apply locally — version was already validated at Prepare time and the
        # key has been locked since, so a mismatch here is unexpected but handled.
        success, new_version = self.store.local_write(key, value, expected_version)

        if not success:
            context.set_code(grpc.StatusCode.ABORTED)
            context.set_details(f"Version mismatch at commit: key='{key}'")

        # Release lock and clean up staging regardless of outcome
        with self._2pc_lock:
            self._staged_writes.pop(tid, None)
            if self._locks.get(key) == tid:
                del self._locks[key]

        if success:
            logger.info(
                f"[{node_id}] [2PC] Commit SUCCESS | tid={tid} "
                f"key='{key}' new_version={new_version}"
            )
        else:
            logger.error(
                f"[{node_id}] [2PC] Commit FAILED (version mismatch) | tid={tid} key='{key}'"
            )

        return database_pb2.CommitResponse(success=success)
 
    def Abort(self, request, context):
        """
        Phase 2 of 2PC — abort path.
 
        Discard the staged write and release the lock.
        Nothing is written to the real KV store.
        """
        tid     = request.transaction_id
        node_id = os.getenv('NODE_ID', 'unknown')
 
        with self._2pc_lock:
            staged = self._staged_writes.pop(tid, None)
            if staged is not None:
                key = staged["key"]
                if self._locks.get(key) == tid:
                    del self._locks[key]
                logger.info(
                    f"[{node_id}] [2PC] Abort | tid={tid} "
                    f"key='{key}' — staged write discarded"
                )
            else:
                logger.warning(
                    f"[{node_id}] [2PC] Abort called but no staged write for tid={tid}"
                )
 
        return database_pb2.AbortResponse(success=True)
 

def serve():
    peer_addresses = parse_peers()
    node_id = os.getenv('NODE_ID', 'unknown')

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    database_pb2_grpc.add_BooksDatabaseServicer_to_server(
        BooksDatabaseServicer(peer_addresses), server
    )
    server.add_insecure_port('[::]:50055')
    server.start()
    logger.info(f"[{node_id}] Books Database running on port 50055")
    server.wait_for_termination()


if __name__ == '__main__':
    serve()