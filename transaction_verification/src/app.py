import sys
import os
import re
import logging

FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
transaction_verification_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/transaction_verification'))
sys.path.insert(0, transaction_verification_grpc_path)
import transaction_verification_pb2 as transaction_verification
import transaction_verification_pb2_grpc as transaction_verification_grpc

import grpc
from concurrent import futures

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# In-memory store: order_id -> cached order fields and event markers
order_store = {}

# Vector clock store: order_id -> { service_name: int }
vector_clocks = {}

SERVICE_NAME = "transaction_verification"

class TransactionVerificationService(transaction_verification_grpc.TransactionVerificationServiceServicer):

    def _merge_vector_clock(self, order_id, incoming_vc):
        vc = dict(incoming_vc)
        local_counter = vector_clocks[order_id].get(SERVICE_NAME, 0)
        vc[SERVICE_NAME] = max(local_counter, vc.get(SERVICE_NAME, 0)) + 1
        vector_clocks[order_id] = vc
        return vc

    def _is_local_vc_inferior_or_equal(self, order_id, incoming_vc):
        local_vc = vector_clocks.get(order_id, {})
        for key, local_value in local_vc.items():
            if local_value > incoming_vc.get(key, 0):
                return False
        return True

    def InitOrder(self, request, context):
        order_id = request.order_id
        if not order_id:
            logger.warning("InitOrder rejected: missing order_id")
            return transaction_verification.InitOrderResponse(success=False)

        order_store[order_id] = {
            "items": list(request.items),
            "user_name": request.user_name,
            "user_contact": request.user_contact,
            "card_number": request.card_number,
            "expiration_date": request.expiration_date,
            "event_a_validated": False,
        }
        vector_clocks[order_id] = {SERVICE_NAME: 0}
        logger.info(f"[{order_id}] InitOrder cached | VC: {vector_clocks[order_id]}")
        return transaction_verification.InitOrderResponse(success=True)

    # ------------------------------------Event a: Verify Items------------------------------------
    def VerifyItems(self, request, context):
        """
        Verify that the order contains at least one item.
        This is Event a in the transaction verification sequence.
        """
        order_id = request.order_id

        if order_id not in order_store or order_id not in vector_clocks:
            reason = "Order not initialized. Call InitOrder first"
            logger.warning(f"[Event a] Rejected: {reason} | order_id='{order_id}'")
            return transaction_verification.VerifyResponse(
                is_valid=False,
                reason=reason,
                vector_clock=dict(request.vector_clock)
            )

        vc = self._merge_vector_clock(order_id, request.vector_clock)
        items = order_store[order_id]["items"]
        logger.info(f"[{order_id}] Event a: VerifyItems | items: {items} | VC: {vc}")
 
        if not items:
            logger.warning(f"[{order_id}] [Event a] Rejected: empty items list")
            return transaction_verification.VerifyResponse(
                is_valid=False,
                reason="Order must contain at least one item",
                vector_clock=vc
            )

        order_store[order_id]["event_a_validated"] = True
 
        logger.info(f"[{order_id}] [Event a] Approved: {len(items)} item(s) found")
        return transaction_verification.VerifyResponse(is_valid=True, reason="", vector_clock=vc)


    # -----------------------------------Event b: Verify User Data------------------------------------
    def VerifyUserData(self, request, context):
        """
        Verify that mandatory user data fields are present and non-empty.
        Checks: user_name and user_contact.
        This is Event b in the transaction verification sequence.
        """
        order_id = request.order_id

        if order_id not in order_store or order_id not in vector_clocks:
            reason = "Order not initialized. Call InitOrder first"
            logger.warning(f"[Event b] Rejected: {reason} | order_id='{order_id}'")
            return transaction_verification.VerifyResponse(
                is_valid=False,
                reason=reason,
                vector_clock=dict(request.vector_clock)
            )

        vc = self._merge_vector_clock(order_id, request.vector_clock)
        order = order_store[order_id]
        logger.info(
            f"[{order_id}] Event b: VerifyUserData | user_name='{order['user_name']}' user_contact='{order['user_contact']}' | VC: {vc}"
        )
 
        mandatory_fields = {
            "user_name":    order["user_name"],
            "user_contact": order["user_contact"],
        }
 
        missing = [field for field, value in mandatory_fields.items() if not value or not value.strip()]
 
        if missing:
            reason = f"Missing mandatory fields: {', '.join(missing)}"
            logger.warning(f"[{order_id}] [Event b] Rejected: {reason}")
            return transaction_verification.VerifyResponse(is_valid=False, reason=reason, vector_clock=vc)
 
        logger.info(f"[{order_id}] [Event b] Approved: all user data fields are valid")
        return transaction_verification.VerifyResponse(is_valid=True, reason="", vector_clock=vc)
    

    # ------------------------------------Event c: Verify Credit Card------------------------------------
    def VerifyCreditCard(self, request, context):
        """
        Verify that the credit card information is in the correct format:
        - number: exactly 16 digits
        - expiration_date: MM/YY or MM/YYYY format
        """
        order_id = request.order_id

        if order_id not in order_store or order_id not in vector_clocks:
            reason = "Order not initialized. Call InitOrder first"
            logger.warning(f"[Event c] Rejected: {reason} | order_id='{order_id}'")
            return transaction_verification.VerifyResponse(
                is_valid=False,
                reason=reason,
                vector_clock=dict(request.vector_clock)
            )

        vc = self._merge_vector_clock(order_id, request.vector_clock)
        order = order_store[order_id]

        if not order.get("event_a_validated", False):
            reason = "Causal guard failed: VerifyItems (event a) must succeed before VerifyCreditCard (event c)"
            logger.warning(f"[{order_id}] [Event c] Rejected: {reason} | VC: {vc}")
            return transaction_verification.VerifyResponse(is_valid=False, reason=reason, vector_clock=vc)

        logger.info(
            f"[{order_id}] Event c: VerifyCreditCard | number='{order['card_number']}' expiry='{order['expiration_date']}' | VC: {vc}"
        )
    
        # Validate card number: 16 digits (spaces/dashes stripped)
        card_number = re.sub(r'[\s\-]', '', order["card_number"])
        if not re.match(r'^\d{16}$', card_number):
            reason = "Invalid card number: must be 16 digits"
            logger.warning(f"[{order_id}] [Event c] Rejected: {reason}")
            return transaction_verification.VerifyResponse(is_valid=False, reason=reason, vector_clock=vc)
 
        # Validate expiration date: MM/YY or MM/YYYY
        if not re.match(r'^(0[1-9]|1[0-2])\/(\d{2}|\d{4})$', order["expiration_date"]):
            reason = "Invalid expiration date: expected MM/YY or MM/YYYY"
            logger.warning(f"[{order_id}] [Event c] Rejected: {reason}")
            return transaction_verification.VerifyResponse(is_valid=False, reason=reason, vector_clock=vc)
 
        logger.info(f"[{order_id}] [Event c] Approved: credit card format is valid")
        return transaction_verification.VerifyResponse(is_valid=True, reason="", vector_clock=vc)

    def ClearOrder(self, request, context):
        order_id = request.order_id
        incoming_vc = dict(request.vector_clock)

        if order_id not in order_store or order_id not in vector_clocks:
            logger.info(f"[{order_id}] ClearOrder no-op: order not found")
            return transaction_verification.ClearResponse(success=True)

        local_vc = dict(vector_clocks[order_id])
        if self._is_local_vc_inferior_or_equal(order_id, incoming_vc):
            del order_store[order_id]
            del vector_clocks[order_id]
            logger.info(f"[{order_id}] ClearOrder applied | local_vc={local_vc} incoming_vc={incoming_vc}")
            return transaction_verification.ClearResponse(success=True)
        else:
            logger.warning(f"[{order_id}] ClearOrder rejected: local VC ahead of incoming | local_vc={local_vc} incoming_vc={incoming_vc}")
            return transaction_verification.ClearResponse(success=False)

def serve():
    """Start gRPC server on port 50052."""
    server = grpc.server(futures.ThreadPoolExecutor())
    transaction_verification_grpc.add_TransactionVerificationServiceServicer_to_server(TransactionVerificationService(), server)
    
    port = "50052"
    server.add_insecure_port("[::]:" + port)
    server.start()
    logger.info("Transaction Verification server started. Listening on port 50052.")
    server.wait_for_termination()

if __name__ == '__main__':
    serve()
