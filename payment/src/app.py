import os
import sys
import logging
import threading
import json
import time
from pathlib import Path

import grpc
from concurrent import futures

FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
payment_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/payment'))
sys.path.insert(0, payment_grpc_path)
import payment_pb2
import payment_pb2_grpc

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Persistance file for staged transactions
STAGED_PAYMENTS_FILE = "/tmp/staged_payments.json"


class PaymentService(payment_pb2_grpc.PaymentServiceServicer):
    """
    Payment Service implementing Two-Phase Commit (2PC) protocol.
    
    Phases:
    1. Prepare: Vote YES to commit the transaction, persist the state
    2. Commit: Execute payment, remove from staging
    3. Abort: Cancel payment, remove from staging
    """

    def __init__(self):
        self.port = int(os.getenv("PAYMENT_PORT", "50061"))
        # Lock for thread-safe transaction management
        self.lock = threading.Lock()
        # In-memory map of staged transactions: transaction_id -> {status, amount, user}
        self.staged_transactions = {}
        # Load any previously staged transactions from file
        self._load_staged_payments()

    def _load_staged_payments(self):
        """
        Recovery: Load staged payments from disk at startup.
        This handles the case where the service crashed after PREPARE but before COMMIT.
        """
        if not os.path.exists(STAGED_PAYMENTS_FILE):
            logger.info(f"No staged payments file found at {STAGED_PAYMENTS_FILE}")
            return

        try:
            with open(STAGED_PAYMENTS_FILE, 'r') as f:
                data = json.load(f)
                self.staged_transactions = data
                logger.warning(
                    f"[RECOVERY] Loaded {len(data)} staged transactions from disk: "
                    f"{list(data.keys())}"
                )
                for tx_id, tx_info in data.items():
                    logger.warning(
                        f"[RECOVERY] Transaction {tx_id}: status={tx_info.get('status')}, "
                        f"amount={tx_info.get('amount')}, user={tx_info.get('user')}"
                    )
        except Exception as e:
            logger.error(f"Failed to load staged payments: {e}")

    def _persist_staged_payments(self):
        """
        Persist current staged transactions to disk.
        Called after Prepare phase to ensure durability.
        """
        try:
            with open(STAGED_PAYMENTS_FILE, 'w') as f:
                json.dump(self.staged_transactions, f, indent=2)
            logger.debug(f"Persisted {len(self.staged_transactions)} staged transactions to disk")
        except Exception as e:
            logger.error(f"Failed to persist staged payments: {e}")

    def Prepare(self, request, context):
        """
        Phase 1: Prepare for commit.
        
        - Validate the transaction
        - Vote YES to participate in commit
        - Persist the prepared state
        - Return VOTE_YES to coordinator
        """
        transaction_id = request.transaction_id
        user_name = request.user_name
        amount = request.amount

        logger.info(
            f"[PREPARE] Received prepare request: transaction_id={transaction_id}, "
            f"user={user_name}, amount={amount}"
        )

        with self.lock:
            # Check if we already have this transaction
            if transaction_id in self.staged_transactions:
                logger.warning(
                    f"[PREPARE] Transaction {transaction_id} already staged. "
                    f"Status: {self.staged_transactions[transaction_id].get('status')}"
                )
                # Already prepared, return yes
                return payment_pb2.PrepareResponse(vote_yes=True, error="")

            # Simple validation: amount must be positive
            if amount <= 0:
                logger.error(f"[PREPARE] Invalid amount: {amount}")
                return payment_pb2.PrepareResponse(
                    vote_yes=False,
                    error="Amount must be positive"
                )

            # Stage the transaction
            self.staged_transactions[transaction_id] = {
                "status": "PREPARED",
                "amount": amount,
                "user": user_name
            }

            # Persist to disk
            self._persist_staged_payments()

            logger.info(
                f"[PREPARE] ✓ Voted YES for transaction {transaction_id}. "
                f"Staged for commit."
            )
            '''
            # Bonus Task : Demo crash after prepare 
            logger.info("[DEMO] Waiting to crash...")
            time.sleep(100)
            '''

            return payment_pb2.PrepareResponse(vote_yes=True, error="")

    def Commit(self, request, context):
        """
        Phase 2: Commit the transaction.
        
        - Execute the payment
        - Remove from staged transactions
        - Return success
        """
        transaction_id = request.transaction_id

        logger.info(f"[COMMIT] Received commit request: transaction_id={transaction_id}")

        with self.lock:
            if transaction_id not in self.staged_transactions:
                logger.warning(
                    f"[COMMIT] Transaction {transaction_id} not found in staged payments. "
                    f"It may have already been committed or aborted."
                )
                # Consider this a success (idempotent)
                return payment_pb2.CommitResponse(success=True)

            tx_info = self.staged_transactions[transaction_id]
            amount = tx_info.get("amount")
            user = tx_info.get("user")

            # Execute payment (simulated)
            logger.info(
                f"[COMMIT] ✓ Payment executed for order {transaction_id}: "
                f"user={user}, amount={amount:.2f}"
            )

            # Remove from staged transactions
            del self.staged_transactions[transaction_id]

            # Persist the updated state
            self._persist_staged_payments()

            return payment_pb2.CommitResponse(success=True)

    def Abort(self, request, context):
        """
        Phase 3 (or early abort): Rollback the transaction.
        
        - Discard the prepared state
        - Remove from staged transactions
        - Return success
        """
        transaction_id = request.transaction_id

        logger.info(f"[ABORT] Received abort request: transaction_id={transaction_id}")

        with self.lock:
            if transaction_id not in self.staged_transactions:
                logger.warning(
                    f"[ABORT] Transaction {transaction_id} not found in staged payments. "
                    f"Nothing to abort."
                )
                # Consider this a success (idempotent)
                return payment_pb2.AbortResponse(success=True)

            tx_info = self.staged_transactions[transaction_id]
            amount = tx_info.get("amount")
            user = tx_info.get("user")

            logger.info(
                f"[ABORT] ✓ Payment aborted for order {transaction_id}: "
                f"user={user}, amount={amount:.2f}"
            )

            # Remove from staged transactions
            del self.staged_transactions[transaction_id]

            # Persist the updated state
            self._persist_staged_payments()

        

            return payment_pb2.AbortResponse(success=True)


def serve():
    """Start the gRPC server."""
    port = os.getenv("PAYMENT_PORT", "50061")
    
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    payment_pb2_grpc.add_PaymentServiceServicer_to_server(PaymentService(), server)
    server.add_insecure_port(f"[::]:{port}")
    
    logger.info(f"Payment Service starting on port {port}")
    server.start()
    logger.info(f"Payment Service listening on 0.0.0.0:{port}")
    
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Payment Service shutting down...")
        server.stop(0)


if __name__ == "__main__":
    serve()
