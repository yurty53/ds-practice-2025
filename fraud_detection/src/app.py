import sys
import os

# This set of lines are needed to import the gRPC stubs.
# The path of the stubs is relative to the current file, or absolute inside the container.
# Change these lines only if strictly needed.
FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
fraud_detection_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/fraud_detection'))
sys.path.insert(0, fraud_detection_grpc_path)
import fraud_detection_pb2 as fraud_detection
import fraud_detection_pb2_grpc as fraud_detection_grpc

import grpc
from concurrent import futures
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# Create a class to define the server functions, derived from
# fraud_detection_pb2_grpc.FraudDetectionserviceServicer
class FraudDetectionService(fraud_detection_grpc.FraudDetectionserviceServicer):
    def CheckFraud(self, request, context):
        """Check if transaction is fraudulent. Test card "1234-5678-9012-3456" is flagged."""
        logger.info(f"Received request | card: {request.card_number} | amount: {request.order_amount}")
    
        is_fraud = request.card_number == "1234-5678-9012-3456"
    
        if is_fraud:
            logger.warning("Fraud detected: test card number flagged")
        else:
            logger.info("No fraud detected")
    
        response = fraud_detection.FraudResponse()
        response.is_fraud = is_fraud
        return response

def serve():
    """Start gRPC server on port 50051."""
    server = grpc.server(futures.ThreadPoolExecutor())
    fraud_detection_grpc.add_FraudDetectionserviceServicer_to_server(FraudDetectionService(), server)
    
    port = "50051"
    server.add_insecure_port("[::]:" + port)
    server.start()
    logger.info("Fraud Detection server started. Listening on port 50051.")
    server.wait_for_termination()

if __name__ == '__main__':
    serve()