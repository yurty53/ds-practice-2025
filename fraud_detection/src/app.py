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

# Create a class to define the server functions, derived from
# fraud_detection_pb2_grpc.HelloServiceServicer
class FraudDetectionService(fraud_detection_grpc.FraudDetectionserviceServicer):
    # Create an RPC function to say hello
    def SayHello(self, request, context):
        print(f"Received request - Card: {request.card_number}, Amount: {request.order_amount}")
    
        # Flag a specific card number as fraudulent for testing
        is_fraud = request.card_number == "1234-5678-9012-3456"
    
        if is_fraud:
            print("Fraud detected: test card number flagged")
    
        response = fraud_detection.FraudResponse()
        response.is_fraud = is_fraud
        return response

def serve():
    server = grpc.server(futures.ThreadPoolExecutor())
    fraud_detection_grpc.add_FraudDetectionserviceServicer_to_server(FraudDetectionService(), server)
    
    port = "50051"
    server.add_insecure_port("[::]:" + port)
    server.start()
    print("Fraud Detection server started. Listening on port 50051.")
    server.wait_for_termination()

if __name__ == '__main__':
    serve()