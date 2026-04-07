import sys
import os

FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
fraud_detection_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/fraud_detection'))
sys.path.insert(0, fraud_detection_grpc_path)
import fraud_detection_pb2 as fraud_detection
import fraud_detection_pb2_grpc as fraud_detection_grpc

import grpc
from concurrent import futures
import logging
import re
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# In-memory store: order_id -> { user_name, user_contact, card_number, expiration_date }
order_store = {}

# Vector clock store: order_id -> { service_name: int }
vector_clocks = {}

SERVICE_NAME = "fraud_detection"


def luhn_check(card_number):
    digits = [int(d) for d in card_number if d.isdigit()]
    digits.reverse()
    total = 0
    for i, d in enumerate(digits):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def check_user_fraud(name, contact):
    if not re.match(r'^[A-Za-z\s]+$', name):
        return True, "Name contains invalid characters"
    if '@' not in contact or '.' not in contact.split('@')[-1]:
        return True, "Contact is not a valid email address"
    return False, ""


def check_card_fraud(card_number, expiration_date):
    try:
        parts = expiration_date.replace(' ', '').split('/')
        month = int(parts[0])
        year = 2000 + int(parts[1])
    except (ValueError, IndexError):
        return True, "Expiration date format is invalid"

    if not (1 <= month <= 12):
        return True, "Expiration month is invalid"

    now = datetime.now()
    if year < now.year or (year == now.year and month < now.month):
        return True, "Card is expired"

    if not luhn_check(card_number):
        return True, "Card number failed Luhn check"

    return False, ""


class FraudDetectionService(fraud_detection_grpc.FraudDetectionServiceServicer):

    def _is_local_vc_inferior_or_equal(self, order_id, incoming_vc):
        local_vc = vector_clocks.get(order_id, {})
        for key, local_value in local_vc.items():
            if local_value > incoming_vc.get(key, 0):
                return False
        return True

    def InitOrder(self, request, context):
        order_id = request.order_id
        order_store[order_id] = {
            "user_name": request.user_name,
            "user_contact": request.user_contact,
            "card_number": request.card_number,
            "expiration_date": request.expiration_date,
        }
        vector_clocks[order_id] = {SERVICE_NAME: 0}
        logger.info(f"[{order_id}] Order cached | VC: {vector_clocks[order_id]}")
        return fraud_detection.InitOrderResponse(success=True)

    def CheckUserFraud(self, request, context):
        order_id = request.order_id

        # Merge incoming vector clock and increment own
        vc = dict(request.vector_clock)
        vc[SERVICE_NAME] = max(vc.get(SERVICE_NAME, 0), vector_clocks[order_id][SERVICE_NAME]) + 1
        vector_clocks[order_id] = vc
        logger.info(f"[{order_id}] Event d: CheckUserFraud | VC: {vc}")

        order = order_store[order_id]
        is_fraud, reason = check_user_fraud(order["user_name"], order["user_contact"])

        if is_fraud:
            logger.warning(f"[{order_id}] User fraud detected: {reason}")
        else:
            logger.info(f"[{order_id}] User fraud check passed")

        return fraud_detection.FraudEventResponse(
            is_fraud=is_fraud,
            reason=reason,
            vector_clock=vc
        )

    def CheckCreditCardFraud(self, request, context):
        order_id = request.order_id

        # Merge incoming vector clock and increment own
        vc = dict(request.vector_clock)
        vc[SERVICE_NAME] = max(vc.get(SERVICE_NAME, 0), vector_clocks[order_id][SERVICE_NAME]) + 1
        vector_clocks[order_id] = vc
        logger.info(f"[{order_id}] Event e: CheckCreditCardFraud | VC: {vc}")

        order = order_store[order_id]
        is_fraud, reason = check_card_fraud(order["card_number"], order["expiration_date"])

        if is_fraud:
            logger.warning(f"[{order_id}] Card fraud detected: {reason}")
        else:
            logger.info(f"[{order_id}] Card fraud check passed")

        return fraud_detection.FraudEventResponse(
            is_fraud=is_fraud,
            reason=reason,
            vector_clock=vc
        )

    def ClearOrder(self, request, context):
        order_id = request.order_id
        incoming_vc = dict(request.vector_clock)

        if order_id not in order_store or order_id not in vector_clocks:
            logger.info(f"[{order_id}] ClearOrder no-op: order not found")
            return fraud_detection.ClearResponse(success=True)

        local_vc = dict(vector_clocks[order_id])
        if self._is_local_vc_inferior_or_equal(order_id, incoming_vc):
            del order_store[order_id]
            del vector_clocks[order_id]
            logger.info(f"[{order_id}] ClearOrder applied | local_vc={local_vc} incoming_vc={incoming_vc}")
            return fraud_detection.ClearResponse(success=True)
        else:
            logger.warning(f"[{order_id}] ClearOrder rejected: local VC ahead of incoming | local_vc={local_vc} incoming_vc={incoming_vc}")
            return fraud_detection.ClearResponse(success=False)


def serve():
    server = grpc.server(futures.ThreadPoolExecutor())
    fraud_detection_grpc.add_FraudDetectionServiceServicer_to_server(FraudDetectionService(), server)

    port = "50051"
    server.add_insecure_port("[::]:" + port)
    server.start()
    logger.info("Fraud Detection server started. Listening on port 50051.")
    server.wait_for_termination()


if __name__ == '__main__':
    serve()