import sys
import os
import re

FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
transaction_verification_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/transaction_verification'))
sys.path.insert(0, transaction_verification_grpc_path)
import transaction_verification_pb2 as transaction_verification
import transaction_verification_pb2_grpc as transaction_verification_grpc

import grpc
from concurrent import futures

class TransactionVerificationService(transaction_verification_grpc.TransactionVerificationServiceServicer):
    def VerifyTransaction(self, request, context):
        print(f"Received request - Card: {request.card_number}, Items: {request.items}")
    
        if not request.items:
            is_valid = False
            reason = "No items in order"
            print(f"Transaction rejected: {reason}")
        elif not re.match(r'^\d{4}-\d{4}-\d{4}-\d{4}$', request.card_number):
            is_valid = False
            reason = "Invalid card number format"
            print(f"Transaction rejected: {reason}")
        else:
            is_valid = True
            reason = ""
            print("Transaction verified successfully")
            
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
    print("Transaction Verification server started. Listening on port 50052.")
    server.wait_for_termination()

if __name__ == '__main__':
    serve()