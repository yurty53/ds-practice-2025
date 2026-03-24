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
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# Orchestrator-side vector clock store: order_id -> { service_name: counter }
order_vc = {}


def init_all_services(order_id, order_data):
    """
    Initialize order state on all downstream services in parallel.

    Raises:
        RuntimeError: if any service InitOrder fails or is not available.
    """
    user = order_data.get('user', {})
    credit_card = order_data.get('creditCard', {})
    items = [item.get('name', '') for item in order_data.get('items', [])]

    # Initialize orchestrator VC state for this order.
    order_vc[order_id] = {}

    def init_transaction_verification():
        with grpc.insecure_channel('transaction_verification:50052') as channel:
            stub = transaction_verification_grpc.TransactionVerificationServiceStub(channel)
            response = stub.InitOrder(
                transaction_verification.InitOrderRequest(
                    order_id=order_id,
                    items=items,
                    user_name=user.get('name', ''),
                    user_contact=user.get('contact', ''),
                    card_number=credit_card.get('number', ''),
                    expiration_date=credit_card.get('expirationDate', ''),
                )
            )
            if not response.success:
                raise RuntimeError(f"[{order_id}] transaction_verification InitOrder returned success=False")

    def init_fraud_detection():
        with grpc.insecure_channel('fraud_detection:50051') as channel:
            stub = fraud_detection_grpc.FraudDetectionServiceStub(channel)
            response = stub.InitOrder(
                fraud_detection.InitOrderRequest(
                    order_id=order_id,
                    user_name=user.get('name', ''),
                    user_contact=user.get('contact', ''),
                    card_number=credit_card.get('number', ''),
                    expiration_date=credit_card.get('expirationDate', ''),
                )
            )
            if not response.success:
                raise RuntimeError(f"[{order_id}] fraud_detection InitOrder returned success=False")

    def init_suggestions():
        with grpc.insecure_channel('suggestions:50053') as channel:
            stub = suggestions_grpc.SuggestionsServiceStub(channel)
            if not hasattr(stub, 'InitOrder'):
                raise RuntimeError(f"[{order_id}] suggestions InitOrder RPC is not defined")
            response = stub.InitOrder(suggestions.InitOrderRequest(order_id=order_id))
            if not response.success:
                raise RuntimeError(f"[{order_id}] suggestions InitOrder returned success=False")

    tasks = {
        'transaction_verification': init_transaction_verification,
        'fraud_detection': init_fraud_detection,
        'suggestions': init_suggestions,
    }

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(task): name for name, task in tasks.items()}
        for future in as_completed(futures):
            service_name = futures[future]
            try:
                future.result()
                logger.info(f"[{order_id}] {service_name} InitOrder complete")
            except Exception as exc:
                raise RuntimeError(f"[{order_id}] Failed to initialize {service_name}: {exc}") from exc


def merge_clocks(vc1, vc2):
    merged = dict(vc1)
    for key, value in vc2.items():
        merged[key] = max(merged.get(key, 0), value)
    return merged


def run_initial_verifications(order_id):
    """
    Run event (a) VerifyItems and event (b) VerifyUserData in parallel.

    Returns:
        tuple[dict, dict]: (vc_a, vc_b)

    Raises:
        RuntimeError: if event a or b fails.
    """
    vc_empty = {}
    logger.info(f"[{order_id}] Calling Event a with VC: {vc_empty}")
    logger.info(f"[{order_id}] Calling Event b with VC: {vc_empty}")

    with grpc.insecure_channel('transaction_verification:50052') as channel:
        stub = transaction_verification_grpc.TransactionVerificationServiceStub(channel)

        def run_event_a():
            return stub.VerifyItems(
                transaction_verification.VerifyRequest(
                    order_id=order_id,
                    vector_clock={}
                )
            )

        def run_event_b():
            return stub.VerifyUserData(
                transaction_verification.VerifyRequest(
                    order_id=order_id,
                    vector_clock={}
                )
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            future_a = executor.submit(run_event_a)
            future_b = executor.submit(run_event_b)

            resp_a = future_a.result()
            resp_b = future_b.result()

    if not resp_a.is_valid:
        raise RuntimeError(f"[{order_id}] VerifyItems failed: {resp_a.reason}")
    if not resp_b.is_valid:
        raise RuntimeError(f"[{order_id}] VerifyUserData failed: {resp_b.reason}")

    vc_a = dict(resp_a.vector_clock)
    vc_b = dict(resp_b.vector_clock)
    order_vc.setdefault(order_id, {})['vc_a'] = vc_a
    order_vc.setdefault(order_id, {})['vc_b'] = vc_b

    return vc_a, vc_b


# ---OLD CODE---
# def run_fraud_detection(request_data, results):
#     """Call fraud detection InitOrder then CheckUserFraud and CheckCreditCardFraud in sequence."""
#     order_id = str(uuid.uuid4())
#     user_name = request_data.get('user', {}).get('name', '')
#     user_contact = request_data.get('user', {}).get('contact', '')
#     card_number = request_data.get('creditCard', {}).get('number', '')
#     expiration_date = request_data.get('creditCard', {}).get('expirationDate', '')
#
#     with grpc.insecure_channel('fraud_detection:50051') as channel:
#         stub = fraud_detection_grpc.FraudDetectionServiceStub(channel)
#
#         # Init
#         stub.InitOrder(fraud_detection.InitOrderRequest(
#             order_id=order_id,
#             user_name=user_name,
#             user_contact=user_contact,
#             card_number=card_number,
#             expiration_date=expiration_date,
#         ))
#         logger.info(f"[{order_id}] InitOrder complete")
#
#         # Event d
#         vc = {}
#         resp_d = stub.CheckUserFraud(fraud_detection.UserFraudRequest(
#             order_id=order_id,
#             vector_clock=vc
#         ))
#         logger.info(f"[{order_id}] CheckUserFraud: is_fraud={resp_d.is_fraud} | VC={dict(resp_d.vector_clock)}")
#         if resp_d.is_fraud:
#             results['is_fraud'] = True
#             results['fraud_reason'] = resp_d.reason
#             return
#
#         # Event e
#         resp_e = stub.CheckCreditCardFraud(fraud_detection.CardFraudRequest(
#             order_id=order_id,
#             vector_clock=dict(resp_d.vector_clock)
#         ))
#         logger.info(f"[{order_id}] CheckCreditCardFraud: is_fraud={resp_e.is_fraud} | VC={dict(resp_e.vector_clock)}")
#         results['is_fraud'] = resp_e.is_fraud
#         results['fraud_reason'] = resp_e.reason


def run_event_c(order_id):
    vc_a, vc_b = run_initial_verifications(order_id)
    vc_for_c = merge_clocks(vc_a, vc_b)
    logger.info(f"[{order_id}] Merging VC for Event c from vc_a={vc_a} and vc_b={vc_b}")
    logger.info(f"[{order_id}] Calling Event c with VC: {vc_for_c}")

    with grpc.insecure_channel('transaction_verification:50052') as channel:
        stub = transaction_verification_grpc.TransactionVerificationServiceStub(channel)
        resp_c = stub.VerifyCreditCard(
            transaction_verification.VerifyRequest(
                order_id=order_id,
                vector_clock=vc_for_c,
            )
        )

    if not resp_c.is_valid:
        raise RuntimeError(f"[{order_id}] VerifyCreditCard failed: {resp_c.reason}")

    vc_c = dict(resp_c.vector_clock)
    order_vc.setdefault(order_id, {})['vc_c'] = vc_c
    return vc_c


def run_event_d(order_id):
    vc_for_d = {}
    logger.info(f"[{order_id}] Calling Event d with VC: {vc_for_d}")

    with grpc.insecure_channel('fraud_detection:50051') as channel:
        stub = fraud_detection_grpc.FraudDetectionServiceStub(channel)
        resp_d = stub.CheckUserFraud(
            fraud_detection.UserFraudRequest(
                order_id=order_id,
                vector_clock=vc_for_d,
            )
        )

    if resp_d.is_fraud:
        raise RuntimeError(f"[{order_id}] CheckUserFraud failed: {resp_d.reason}")

    vc_d = dict(resp_d.vector_clock)
    order_vc.setdefault(order_id, {})['vc_d'] = vc_d
    return vc_d


def run_event_e(order_id, vc_c, vc_d):
    vc_for_e = merge_clocks(vc_c, vc_d)
    logger.info(f"[{order_id}] Calling Event e with VC: {vc_for_e}")

    with grpc.insecure_channel('fraud_detection:50051') as channel:
        stub = fraud_detection_grpc.FraudDetectionServiceStub(channel)
        resp_e = stub.CheckCreditCardFraud(
            fraud_detection.CardFraudRequest(
                order_id=order_id,
                vector_clock=vc_for_e,
            )
        )

    if resp_e.is_fraud:
        raise RuntimeError(f"[{order_id}] CheckCreditCardFraud failed: {resp_e.reason}")

    vc_e = dict(resp_e.vector_clock)
    order_vc.setdefault(order_id, {})['vc_e'] = vc_e
    return vc_e


def run_event_f(order_id, book_titles, vc_e):
    logger.info(f"[{order_id}] Calling Event f with VC: {vc_e}")

    with grpc.insecure_channel('suggestions:50053') as channel:
        stub = suggestions_grpc.SuggestionsServiceStub(channel)
        response = stub.GetSuggestions(
            suggestions.SuggestionsRequest(
                book_titles=book_titles,
                vector_clock=vc_e,
            )
        )

    final_vc = dict(response.vector_clock)
    order_vc.setdefault(order_id, {})['vc_f'] = final_vc
    suggested_books = [{'title': book.title, 'author': book.author} for book in response.books]
    return suggested_books, final_vc
        
# ---OLD CODE---
# def check_transaction(order_id, credit_card, results):
#     """
#     Call transaction verification gRPC service.
#
#     Events (a) VerifyItems and (b) VerifyUserData run in parallel first.
#     Event (c) VerifyCreditCard runs only after both pass.
#     """
#
#     logger.info(f"Calling transaction verification | order_id={order_id} | card: {credit_card.get('number')}")
#
#     try:
#         vc_c = run_event_c(order_id)
#         results['vc_c'] = vc_c
#         results['is_valid'] = True
#         results['reason'] = ""
#         logger.info(f"Transaction verification passed events a/b/c | order_id={order_id}")
#     except Exception as exc:
#         logger.warning(f"Transaction verification failed: {exc}")
#         results['is_valid'] = False
#         results['reason'] = str(exc)

# ---OLD CODE---
# def get_suggestions(book_titles, results):
#     """Call suggestions gRPC service. Stores result in shared dict."""
#     logger.info(f"Calling suggestions service | titles: {book_titles}")
#     with grpc.insecure_channel('suggestions:50053') as channel:
#         stub = suggestions_grpc.SuggestionsServiceStub(channel)
#         response = stub.GetSuggestions(suggestions.SuggestionsRequest(
#             book_titles=book_titles,
#             vector_clock={}
#         ))
#     results['suggestions'] = [{'title': book.title, 'author': book.author} for book in response.books]
#     logger.info(f"Suggestions result: {results['suggestions']}")


def get_suggestions_for_route(book_titles):
    """Call suggestions service for /suggestions endpoint."""
    logger.info(f"Calling suggestions route helper | titles: {book_titles}")
    with grpc.insecure_channel('suggestions:50053') as channel:
        stub = suggestions_grpc.SuggestionsServiceStub(channel)
        response = stub.GetSuggestions(
            suggestions.SuggestionsRequest(
                book_titles=book_titles,
                vector_clock={}
            )
        )
    return [{'title': book.title, 'author': book.author} for book in response.books]

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
    return {'suggestions': get_suggestions_for_route(book_titles)}

def checkout(order_data):
    """
    Process checkout with causal event ordering across services.

    Flow: d and c, then e with merged VC(vc_c, vc_d), then f with VC from e.
    """
    logger.info(f"Checkout request received | items: {order_data.get('items')}")

    user            = order_data.get('user', {})
    items           = [item.get('name', '') for item in order_data.get('items', [])]
    order_id        = str(uuid.uuid4())

    try:
        init_all_services(order_id, order_data)
    except Exception as exc:
        logger.warning(f"Checkout initialization failed: {exc}")
        return {
            'orderId': order_id,
            'status': 'Order Rejected',
            'suggestedBooks': []
        }
    
    logger.info(f"Processing checkout | user: {user.get('name')} | items: {items}")

    try:
        vc_d = run_event_d(order_id)
        vc_c = run_event_c(order_id)

        logger.info(f"[{order_id}] Merging VC for Event e from vc_c={vc_c} and vc_d={vc_d}")
        vc_e = run_event_e(order_id, vc_c, vc_d)

        suggested_books, final_vc = run_event_f(order_id, items, vc_e)
    except Exception as exc:
        logger.warning(f"Checkout failed in causal flow: {exc}")
        order_status_response = {
            'orderId': order_id,
            'status': 'Order Rejected',
            'suggestedBooks': [],
            'vectorClock': order_vc.get(order_id, {})
        }
    else:
        logger.info("Order approved")
        order_status_response = {
            'orderId': order_id,
            'status': 'Order Approved',
            'suggestedBooks': suggested_books,
            'vectorClock': final_vc,
        }

    return order_status_response


@app.route('/checkout', methods=['POST'])
def checkout_route():
    return checkout(json.loads(request.data))


if __name__ == '__main__':
    # Run the app in debug mode to enable hot reloading.
    # This is useful for development.
    # The default port is 5000.
    app.run(host='0.0.0.0')
