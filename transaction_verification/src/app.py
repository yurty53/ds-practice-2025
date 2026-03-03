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
    def VerifyTransaction(self, request, context):
        logger.info(f"Received request | card: {request.card_number} | items: {list(request.items)}")
    
        if not request.items:
            is_valid = False
            reason = "No items in order"
            logger.warning(f"Transaction rejected: {reason}")
        elif not re.match(r'^\d{16}$', request.card_number):
            is_valid = False
            reason = "Invalid card number format"
            logger.warning(f"Transaction rejected: {reason}")
        else:
            is_valid = True
            reason = ""
            logger.info("Transaction verified successfully")
            
        response = transaction_verification.TransactionResponse()
        response.is_valid = is_valid
        response.reason = reason
        return response

def serve():
    server = grpc.server(futures.ThreadPoolExecutor())
    transaction_verification_grpc.add_TransactionVerificationServiceServicer_to_server(TransactionVerificationService(), server)
    
    port = "50052"
    server.add_insecure_port("[::]:" + port)
    server.start()
    logger.info("Transaction Verification server started. Listening on port 50052.")
    server.wait_for_termination()

if __name__ == '__main__':
    serve()