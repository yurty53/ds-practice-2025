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

class TransactionVerificationService(transaction_verification_grpc.TransactionVerificationServiceServicer):

    # ------------------------------------Event a: Verify Items------------------------------------
    def VerifyItems(self, request, context):
        """
        Verify that the order contains at least one item.
        This is Event a in the transaction verification sequence.
        """
        logger.info(f"[Event a] VerifyItems | items: {list(request.items)}")
 
        if not request.items:
            logger.warning("[Event a] Rejected: empty items list")
            return transaction_verification.VerifyResponse(
                is_valid=False,
                reason="Order must contain at least one item"
            )
 
        logger.info(f"[Event a] Approved: {len(request.items)} item(s) found")
        return transaction_verification.VerifyResponse(is_valid=True, reason="")


    # -----------------------------------Event b: Verify User Data------------------------------------
    def VerifyUserData(self, request, context):
        """
        Verify that all mandatory user data fields are present and non-empty.
        Checks: name, contact (email), and billing address fields.
        This is Event b in the transaction verification sequence.
        """
        logger.info(f"[Event b] VerifyUserData | name='{request.name}' contact='{request.contact}' city='{request.city}'")
 
        mandatory_fields = {
            "name":    request.name,
            "contact": request.contact,
            "street":  request.street,
            "city":    request.city,
            "state":   request.state,
            "zip":     request.zip,
            "country": request.country,
        }
 
        missing = [field for field, value in mandatory_fields.items() if not value or not value.strip()]
 
        if missing:
            reason = f"Missing mandatory fields: {', '.join(missing)}"
            logger.warning(f"[Event b] Rejected: {reason}")
            return transaction_verification.VerifyResponse(is_valid=False, reason=reason)
 
        logger.info("[Event b] Approved: all user data fields are valid")
        return transaction_verification.VerifyResponse(is_valid=True, reason="")
    

    # ------------------------------------Event c: Verify Credit Card------------------------------------
    def VerifyCreditCard(self, request, context):
        """
        Verify that the credit card information is in the correct format:
        - number: exactly 16 digits
        - expiration_date: MM/YY format, not expired
        - cvv: 3 or 4 digits
        """
        logger.info(f"[Event c] VerifyCreditCard | number='{request.number}' expiry='{request.expiration_date}' cvv=***")
    
        # Validate card number: 16 digits (spaces/dashes stripped)
        card_number = re.sub(r'[\s\-]', '', request.number)
        if not re.match(r'^\d{16}$', card_number):
            reason = "Invalid card number: must be 16 digits"
            logger.warning(f"[Event c] Rejected: {reason}")
            return transaction_verification.VerifyResponse(is_valid=False, reason=reason)
 
        # Validate expiration date: MM/YY or MM/YYYY
        if not re.match(r'^(0[1-9]|1[0-2])\/(\d{2}|\d{4})$', request.expiration_date):
            reason = "Invalid expiration date: expected MM/YY or MM/YYYY"
            logger.warning(f"[Event c] Rejected: {reason}")
            return transaction_verification.VerifyResponse(is_valid=False, reason=reason)
 
        # Validate CVV: 3 digits
        if not re.match(r'^\d{3}$', request.cvv):
            reason = "Invalid CVV: must be 3 digits"
            logger.warning(f"[Event c] Rejected: {reason}")
            return transaction_verification.VerifyResponse(is_valid=False, reason=reason)
 
        logger.info("[Event c] Approved: credit card format is valid")
        return transaction_verification.VerifyResponse(is_valid=True, reason="")

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
