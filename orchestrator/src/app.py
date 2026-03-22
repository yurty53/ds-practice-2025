import sys
import os
import uuid

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

def run_fraud_detection(request_data, results):
    """Call fraud detection InitOrder then CheckUserFraud and CheckCreditCardFraud in sequence."""
    order_id = str(uuid.uuid4())
    user_name = request_data.get('user', {}).get('name', '')
    user_contact = request_data.get('user', {}).get('contact', '')
    card_number = request_data.get('creditCard', {}).get('number', '')
    expiration_date = request_data.get('creditCard', {}).get('expirationDate', '')

    with grpc.insecure_channel('fraud_detection:50051') as channel:
        stub = fraud_detection_grpc.FraudDetectionServiceStub(channel)

        # Init
        stub.InitOrder(fraud_detection.InitOrderRequest(
            order_id=order_id,
            user_name=user_name,
            user_contact=user_contact,
            card_number=card_number,
            expiration_date=expiration_date,
        ))
        logger.info(f"[{order_id}] InitOrder complete")

        # Event d
        vc = {}
        resp_d = stub.CheckUserFraud(fraud_detection.UserFraudRequest(
            order_id=order_id,
            vector_clock=vc
        ))
        logger.info(f"[{order_id}] CheckUserFraud: is_fraud={resp_d.is_fraud} | VC={dict(resp_d.vector_clock)}")
        if resp_d.is_fraud:
            results['is_fraud'] = True
            results['fraud_reason'] = resp_d.reason
            return

        # Event e
        resp_e = stub.CheckCreditCardFraud(fraud_detection.CardFraudRequest(
            order_id=order_id,
            vector_clock=dict(resp_d.vector_clock)
        ))
        logger.info(f"[{order_id}] CheckCreditCardFraud: is_fraud={resp_e.is_fraud} | VC={dict(resp_e.vector_clock)}")
        results['is_fraud'] = resp_e.is_fraud
        results['fraud_reason'] = resp_e.reason
        
def check_transaction(items, user, billing_address, credit_card, results):
    """
    Call transaction verification gRPC service.
    Executes 3 sequential RPC calls, each representing a distinct event
    to support vector clock ordering in future implementations.
 
    Event 1 — VerifyItems:      items list is not empty
    Event 2 — VerifyUserData:   mandatory user fields are present and valid
    Event 3 — VerifyCreditCard: credit card format is correct
    """
    
    logger.info(f"Calling transaction verification | card: {credit_card.get('number')} | items: {items}")

    with grpc.insecure_channel('transaction_verification:50052') as channel:
        stub = transaction_verification_grpc.TransactionVerificationServiceStub(channel)

        # ------------------------------Event a: Verify Items------------------------------------
        logger.info("Transaction verification | Event a: VerifyItems")
        resp1 = stub.VerifyItems(
            transaction_verification.VerifyItemsRequest(items=items)
        )
        if not resp1.is_valid:
            logger.warning(f"Transaction verification failed at Event a: {resp1.reason}")
            results['is_valid'] = False
            results['reason']   = resp1.reason
            return
 
        # ------------------------------Event b: Verify User Data------------------------------------
        logger.info("Transaction verification | Event b: VerifyUserData")
        resp2 = stub.VerifyUserData(
            transaction_verification.VerifyUserDataRequest(
                name    = user.get('name', ''),
                contact = user.get('contact', ''),
                street  = billing_address.get('street', ''),
                city    = billing_address.get('city', ''),
                state   = billing_address.get('state', ''),
                zip     = billing_address.get('zip', ''),
                country = billing_address.get('country', ''),
            )
        )
        if not resp2.is_valid:
            logger.warning(f"Transaction verification failed at Event b: {resp2.reason}")
            results['is_valid'] = False
            results['reason']   = resp2.reason
            return
 
        # ------------------------------Event c: Verify Credit Card------------------------------------
        logger.info("Transaction verification | Event 3: VerifyCreditCard")
        resp3 = stub.VerifyCreditCard(
            transaction_verification.VerifyCreditCardRequest(
                number          = credit_card.get('number', ''),
                expiration_date = credit_card.get('expirationDate', ''),
                cvv             = credit_card.get('cvv', ''),
            )
        )
        if not resp3.is_valid:
            logger.warning(f"Transaction verification failed at Event c: {resp3.reason}")
            results['is_valid'] = False
            results['reason']   = resp3.reason
            return
 

    results['is_valid'] = True
    results['reason']   = ""
    logger.info("Transaction verification passed all 3 events")

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

    user            = request_data.get('user', {})
    credit_card     = request_data.get('creditCard', {})
    billing_address = request_data.get('billingAddress', {})
    items           = [item.get('name', '') for item in request_data.get('items', [])]
    
    logger.info(f"Processing checkout | user: {user.get('name')} | items: {items}")

    # Shared dictionary for thread results
    results = {}

    # Create threads for parallel gRPC calls
    t1 = threading.Thread(target=run_fraud_detection, args=(request_data, results))
    t2 = threading.Thread(target=check_transaction, args=(items, user, billing_address, credit_card, results))
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
