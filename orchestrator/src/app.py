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
import threading
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

def check_fraud(card_number, order_amount, results):
    """Call fraud detection gRPC service. Stores result in shared dict."""
    logger.info(f"Calling fraud detection | card: {card_number} | amount: {order_amount}")
    with grpc.insecure_channel('fraud_detection:50051') as channel:
        stub = fraud_detection_grpc.FraudDetectionserviceStub(channel)
        response = stub.CheckFraud(fraud_detection.FraudRequest(
            card_number=card_number,
            order_amount=order_amount
        ))
    results['is_fraud'] = response.is_fraud
    logger.info(f"Fraud detection result: is_fraud={response.is_fraud}")

def check_transaction(card_number, items, results):
    """Call transaction verification gRPC service. Stores result in shared dict."""
    logger.info(f"Calling transaction verification | card: {card_number} | items: {items}")
    with grpc.insecure_channel('transaction_verification:50052') as channel:
        stub = transaction_verification_grpc.TransactionVerificationServiceStub(channel)
        response = stub.VerifyTransaction(transaction_verification.TransactionRequest(
            card_number=card_number,
            items=items
        ))
    results['is_valid'] = response.is_valid
    results['reason'] = response.reason
    logger.info(f"Transaction verification result: is_valid={response.is_valid} | reason={response.reason}")

def get_suggestions(book_titles, results):
    """Call suggestions gRPC service. Stores result in shared dict."""
    logger.info(f"Calling suggestions service | titles: {book_titles}")
    with grpc.insecure_channel('suggestions:50053') as channel:
        stub = suggestions_grpc.SuggestionsServiceStub(channel)
        response = stub.GetSuggestions(suggestions.SuggestionsRequest(
            book_titles=book_titles
        ))
    results['suggestions'] = [{'title': book.title, 'author': book.author} for book in response.books]
    logger.info(f"Suggestions result: {results['suggestions']}")

# Import Flask.
# Flask is a web framework for Python.
# It allows you to build a web application quickly.
# For more information, see https://flask.palletsprojects.com/en/latest/
from flask import Flask, request
from flask_cors import CORS
import json

# Create a simple Flask app.
app = Flask(__name__)
# Enable CORS for the app.
CORS(app, resources={r'/*': {'origins': '*'}})

# Define a GET endpoint.
@app.route('/', methods=['GET'])
def index():
    """
    Responds with 'Hello, [name]' when a GET request is made to '/' endpoint.
    # Test the fraud-detection gRPC service.
    results = {}
    check_fraud(card_number='test', order_amount=0, results=results)
    # Return the response.
    return "Fraud detected: " + str(results.get('is_fraud'))
    """
    return {"status": "ok"}

@app.route('/suggestions', methods=['POST'])
def suggestions_route():
    """Get book recommendations based on cart contents."""
    request_data = json.loads(request.data)
    book_titles = request_data.get('book_titles', [])
    results = {}
    get_suggestions(book_titles, results)
    return {'suggestions': results.get('suggestions', [])}

@app.route('/checkout', methods=['POST'])
def checkout():
    """
    Process checkout with parallel gRPC calls.
    
    Uses threading to call fraud detection, transaction verification, 
    and suggestions services concurrently for better performance.
    """
    request_data = json.loads(request.data)
    logger.info(f"Checkout request received | items: {request_data.get('items')}")

    card_number = request_data.get('creditCard', {}).get('number', '')
    order_amount = sum(item.get('quantity', 1) for item in request_data.get('items', []))
    items = [item.get('name', '') for item in request_data.get('items', [])]
    
    logger.info(f"Processing checkout | card: {card_number} | items: {items}")

    # Shared dictionary for thread results
    results = {}

    # Create threads for parallel gRPC calls
    t1 = threading.Thread(target=check_fraud, args=(card_number, order_amount, results))
    t2 = threading.Thread(target=check_transaction, args=(card_number, items, results))
    t3 = threading.Thread(target=get_suggestions, args=(items, results))

    # Start all threads
    t1.start()
    t2.start()
    t3.start()

    # Wait for all threads to complete
    t1.join()
    t2.join()
    t3.join()

    logger.info(f"All services responded | fraud={results.get('is_fraud')} | valid={results.get('is_valid')}")

    # Reject if fraud or invalid, otherwise approve
    if results.get('is_fraud') or not results.get('is_valid'):
        order_status_response = {
            'orderId': '12345',
            'status': 'Order Rejected',
            'suggestedBooks': []
        }
    else:
        logger.info("Order approved")
        order_status_response = {
            'orderId': '12345',
            'status': 'Order Approved',
            'suggestedBooks': results.get('suggestions', [])
        }

    return order_status_response


if __name__ == '__main__':
    # Run the app in debug mode to enable hot reloading.
    # This is useful for development.
    # The default port is 5000.
    app.run(host='0.0.0.0')
