import sys
import os
import threading

import grpc
from concurrent import futures

os.environ["SERVICE_NAME"] = "order-queue"

FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
order_queue_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/order_queue'))
sys.path.insert(0, order_queue_grpc_path)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(FILE), '..', '..')))
from utils.monitoring import tracer, queue_size_counter, enqueue_total, dequeue_total
import order_queue_pb2 as order_queue
import order_queue_pb2_grpc as order_queue_grpc

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


class OrderQueueService(order_queue_grpc.OrderQueueServicer):

    def __init__(self):
        self.queue = []
        self.lock = threading.Lock()
        self.current_leader = ""

    def Enqueue(self, request, context):
        with tracer.start_as_current_span("order_queue.enqueue") as span:
            span.set_attribute("order.id", request.order.order_id)
            with self.lock:
                self.queue.append(request.order)
                logger.info(f"Enqueued order {request.order.order_id} | queue size: {len(self.queue)}")
                queue_size_counter.add(1)
                enqueue_total.add(1)
            return order_queue.EnqueueResponse(success=True)

    def Dequeue(self, request, context):
        with tracer.start_as_current_span("order_queue.dequeue") as span:
            with self.lock:
                if request.caller_id:
                    self.current_leader = request.caller_id
                if not self.queue:
                    logger.info("Dequeue called but queue is empty")
                    span.set_attribute("dequeue.hit", False)
                    return order_queue.DequeueResponse(success=False)
                order = self.queue.pop(0)
                logger.info(f"Dequeued order {order.order_id} | queue size: {len(self.queue)}")
                queue_size_counter.add(-1)
                dequeue_total.add(1)
                span.set_attribute("dequeue.hit", True)
                span.set_attribute("order.id", order.order_id)
                return order_queue.DequeueResponse(success=True, order=order)

    def GetLeader(self, request, context):
        with self.lock:
            return order_queue.GetLeaderResponse(leader_id=self.current_leader)


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    order_queue_grpc.add_OrderQueueServicer_to_server(OrderQueueService(), server)
    server.add_insecure_port('[::]:50054')
    logger.info("Order Queue service started on port 50054")
    server.start()
    server.wait_for_termination()


if __name__ == '__main__':
    serve()