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

transaction_verification_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/transaction_verification'))
sys.path.insert(0, transaction_verification_grpc_path)
import transaction_verification_pb2 as transaction_verification
import transaction_verification_pb2_grpc as transaction_verification_grpc

suggestions_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/suggestions'))
sys.path.insert(0, suggestions_grpc_path)
import suggestions_pb2 as suggestions
import suggestions_pb2_grpc as suggestions_grpc

import grpc

def check_fraud(card_number, order_amount):
    # Establish a connection with the fraud-detection gRPC service.
    with grpc.insecure_channel('fraud_detection:50051') as channel:
        # Create a stub object.
        stub = fraud_detection_grpc.FraudDetectionserviceStub(channel)
        # Call the service through the stub object.
        response = stub.SayHello(fraud_detection.FraudRequest(
            card_number=card_number,
            order_amount=order_amount
        ))
    return response.is_fraud

def check_transaction(card_number, items):
    with grpc.insecure_channel('transaction_verification:50052') as channel:
        stub = transaction_verification_grpc.TransactionVerificationServiceStub(channel)
        response = stub.VerifyTransaction(transaction_verification.TransactionRequest(
            card_number=card_number,
            items=items
        ))
    return response.is_valid, response.reason


def get_suggestions(items):
    """Call suggestions gRPC service and return a list of dicts for suggested books."""
    with grpc.insecure_channel('suggestions:50053') as channel:
        stub = suggestions_grpc.SuggestionserviceStub(channel)
        # Build request
        req = suggestions.SuggestionRequest(items=items)
        resp = stub.GetSuggestions(req)
    # Convert response objects to plain dicts
    out = []
    for b in resp.suggestedBooks:
        out.append({'bookId': b.bookId, 'title': b.title, 'author': b.author})
    return out

# Import Flask.
# Flask is a web framework for Python.
# It allows you to build a web application quickly.
# For more information, see https://flask.palletsprojects.com/en/latest/
from flask import Flask, request
from flask_cors import CORS
import json
from concurrent.futures import ThreadPoolExecutor
import logging

# Create a simple Flask app.
app = Flask(__name__)
# Enable CORS for the app.
CORS(app, resources={r'/*': {'origins': '*'}})

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# Define a GET endpoint.
@app.route('/', methods=['GET'])
def index():
    """
    Responds with 'Hello, [name]' when a GET request is made to '/' endpoint.
    """
    # Test the fraud-detection gRPC service.
    response = check_fraud(card_number='test', order_amount=0)
    # Return the response.
    return "Fraud detected: " + str(response)

@app.route('/checkout', methods=['POST'])
def checkout():
    """
    Responds with a JSON object containing the order ID, status, and suggested books.
    """
    # Get request object data to json
    request_data = json.loads(request.data)
    # Print request object data
    app.logger.info("Request Data: %s", request_data.get('items'))

    # Call fraud detection service
    card_number = request_data.get('creditCard', {}).get('number', '')
    # Fake an order amount based on the number of items
    order_amount = sum(item.get('quantity', 1) for item in request_data.get('items', []))
    items = [item.get('name', '') for item in request_data.get('items', [])]
    
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_fraud = executor.submit(check_fraud, card_number, order_amount)
        future_transaction = executor.submit(check_transaction, card_number, items)
        future_suggestions = executor.submit(get_suggestions, items)

        is_fraud = future_fraud.result()
        is_valid, reason = future_transaction.result()
        suggested = future_suggestions.result()

    # Dummy response following the provided YAML specification for the bookstore
    if not is_valid:
        order_status_response = {
            'orderId': '12345',
            'status': 'Order Rejected',
            'suggestedBooks': []
        }
    elif is_fraud:
        order_status_response = {
            'orderId': '12345',
            'status': 'Order Rejected',
            'reason': 'fraud_detected',
            'suggestedBooks': []
        }
    else:
        order_status_response = {
            'orderId': '12345',
            'status': 'Order Approved',
            'suggestedBooks': suggested
        }

    return order_status_response


if __name__ == '__main__':
    # Run the app in debug mode to enable hot reloading.
    # This is useful for development.
    # The default port is 5000.
    app.run(host='0.0.0.0')
