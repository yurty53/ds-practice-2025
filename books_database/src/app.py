import sys
import os
import grpc
import logging
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
        self.peers = peer_addresses
        node_id = os.getenv('NODE_ID', 'unknown')
        logger.info(f"[{node_id}] Books Database started | peers={self.peers}")

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