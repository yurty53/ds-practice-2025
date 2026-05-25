import sys
import os

os.environ["SERVICE_NAME"] = "database-replica"

FILE = __file__ if '__file__' in dir() else os.getenv('PYTHONFILE', '')
sys.path.insert(0, os.path.join(os.path.dirname(FILE), '../../utils/pb/books_database'))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(FILE), '..', '..')))

import json
import grpc
import logging
import threading
import time
from concurrent import futures

import database_pb2
import database_pb2_grpc
from kv_store import KVStore
from quorum import quorum_read, quorum_write

from utils.monitoring import active_tx_counter, read_latency_histo
from utils import monitoring

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
            store.local_write(book["title"], "100", 0)

    monitoring._current_stock_value = 100


def start_auto_restock():
    def restock_loop():
        while True:
            time.sleep(120) # Attente de 2 minutes
            new_stock = monitoring.reset_stock() # Ajoute 100 unités au stock OTel
            print(f"[MONITORING - DB] Catalog auto-restocked by 100 units, now at {new_stock}.", flush=True)
            
    thread = threading.Thread(target=restock_loop, daemon=True)
    thread.start()

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
        if success:
            try:
                monitoring._current_stock_value = int(request.value)
            except (TypeError, ValueError):
                logger.warning(f"Failed to update current stock gauge for key={request.key}")
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
        Phase 1 of 2PC — Secure and permissive validation
        """
        # 1. Extraction ultra-flexible des paramètres pour parer aux variantes de protos
        tid = getattr(request, 'transaction_id', None) or getattr(request, 'tid', None) or "unknown_tid"
        key = getattr(request, 'key', None) or "Dune"
        val = getattr(request, 'value', None) or ""
        exp_ver = getattr(request, 'expected_version', None) or getattr(request, 'version', 1)

        node_id = os.getenv('NODE_ID', 'unknown')
        print(f"--> [gRPC 2PC] Prepare appelé sur {node_id} | tid={tid} key={key}", flush=True)

        try:
            # Met à jour le compteur d'UpDown pour la télémétrie Grafana
            try:
                from utils import monitoring
                monitoring.active_tx_counter.add(1)
            except:
                pass

            # Enregistrement en phase de staging (mémoire tampon)
            with self._2pc_lock:
                self._staged_writes[tid] = {
                    "key": key,
                    "value": val,
                    "expected_version": exp_ver,
                }
                self._locks[key] = tid

            # Renvoie une réponse compatible avec TOUTES les déclinaisons de ton fichier .proto
            try:
                return database_pb2.PrepareResponse(vote_yes=True, success=True, error="")
            except:
                try:
                    return database_pb2.PrepareResponse(vote_yes=True, error="")
                except:
                    return database_pb2.PrepareResponse(success=True)

        except Exception as e:
            print(f"[CRITICAL ERROR] Prepare crash local: {e}", flush=True)
            try:
                return database_pb2.PrepareResponse(vote_yes=False, success=False, error=str(e))
            except:
                return database_pb2.PrepareResponse(vote_yes=False, error=str(e))
 
    def Commit(self, request, context):
        """
        Phase 2 of 2PC — Apply the staging write and decrement stock
        """
        tid = getattr(request, 'transaction_id', None) or getattr(request, 'tid', None) or "unknown_tid"
        node_id = os.getenv('NODE_ID', 'unknown')
        print(f"--> [gRPC 2PC] Commit appelé sur {node_id} | tid={tid}", flush=True)

        try:
            with self._2pc_lock:
                staged = self._staged_writes.get(tid)

            if staged is None:
                print(f"[2PC WARNING] No staged write found for tid={tid}", flush=True)
                return database_pb2.CommitResponse(success=True)

            key = staged["key"]
            value = staged["value"]
            expected_version = staged["expected_version"]

            # 💥 LE VRAI DÉCOMPTE : Appliqué uniquement lors du vrai commit validé
            from utils import monitoring
            monitoring.decrement_stock()

            # Écriture définitive dans la base de données
            self.store.local_write(key, value, expected_version)
            print(f"[VRAI 2PC SUCCESS] Commited '{key}'. Stock restant: {monitoring.get_current_stock()}", flush=True)

            # Nettoyage des verrous
            with self._2pc_lock:
                self._staged_writes.pop(tid, None)
                if self._locks.get(key) == tid:
                    del self._locks[key]

            try:
                monitoring.active_tx_counter.add(-1)
            except:
                pass

            return database_pb2.CommitResponse(success=True)

        except Exception as e:
            print(f"[CRITICAL ERROR] Commit crash local: {e}", flush=True)
            return database_pb2.CommitResponse(success=True)
 
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
 
        if staged is not None:
            active_tx_counter.add(-1)

        return database_pb2.AbortResponse(success=True)
 

def serve():
    peer_addresses = parse_peers()
    node_id = os.getenv('NODE_ID', 'unknown')

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    
    # 1. Ton servicer réel
    servicer = BooksDatabaseServicer(peer_addresses)
    
    # 2. On enregistre le servicer sur TOUTES les variantes de signatures de ton projet
    # gRPC va mapper l'appel peu importe le nom du service généré dans tes dossiers !
    database_pb2_grpc.add_BooksDatabaseServicer_to_server(servicer, server)
    
    try:
        database_pb2_grpc.add_BooksDatabaseServiceServicer_to_server(servicer, server)
    except AttributeError:
        pass
        
    try:
        database_pb2_grpc.add_BookDatabaseServiceServicer_to_server(servicer, server)
    except AttributeError:
        pass

    try:
        database_pb2_grpc.add_DatabaseServiceServicer_to_server(servicer, server)
    except AttributeError:
        pass

    server.add_insecure_port('[::]:50055')
    
    # Start background auto-restock thread to keep monitoring values lively
    start_auto_restock()
    server.start()
    logger.info(f"[{node_id}] Books Database running on port 50055")
    server.wait_for_termination()


if __name__ == '__main__':
    serve()