import sys
import os
import threading

import grpc
from concurrent import futures

FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
order_queue_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/order_queue'))
sys.path.insert(0, order_queue_grpc_path)
import order_queue_pb2 as order_queue
import order_queue_pb2_grpc as order_queue_grpc

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


class OrderQueueService(order_queue_grpc.OrderQueueServicer):

    def __init__(self):
        self.queue = []
        self.lock = threading.Lock()

    def Enqueue(self, request, context):
        with self.lock:
            self.queue.append(request.order)
            logger.info(f"Enqueued order {request.order.order_id} | queue size: {len(self.queue)}")
        return order_queue.EnqueueResponse(success=True)

    def Dequeue(self, request, context):
        with self.lock:
            if not self.queue:
                logger.info("Dequeue called but queue is empty")
                return order_queue.DequeueResponse(success=False)
            order = self.queue.pop(0)
            logger.info(f"Dequeued order {order.order_id} | queue size: {len(self.queue)}")
            return order_queue.DequeueResponse(success=True, order=order)


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    order_queue_grpc.add_OrderQueueServicer_to_server(OrderQueueService(), server)
    server.add_insecure_port('[::]:50054')
    logger.info("Order Queue service started on port 50054")
    server.start()
    server.wait_for_termination()


if __name__ == '__main__':
    serve()