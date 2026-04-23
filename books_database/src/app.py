import sys
import os
import grpc
from concurrent import futures

FILE = __file__ if '__file__' in dir() else os.getenv('PYTHONFILE', '')
sys.path.insert(0, os.path.join(os.path.dirname(FILE), '../../utils/pb/books_database'))

import database_pb2
import database_pb2_grpc
from kv_store import KVStore

class BooksDatabaseServicer(database_pb2_grpc.BooksDatabaseServicer):
    def __init__(self):
        self.store = KVStore()

    def LocalRead(self, request, context):
        value, version = self.store.local_read(request.key)
        return database_pb2.ReadResponse(
            value=value or "",
            version=version,
            found=value is not None
        )

    def LocalWrite(self, request, context):
        success, version = self.store.local_write(
            request.key,
            request.value,
            request.expected_version
        )
        return database_pb2.WriteResponse(
            success=success,
            version=version,
            error="" if success else "Version mismatch"
        )

    def Read(self, request, context):
        # Placeholder — Julien will replace this with quorum logic
        value, version = self.store.local_read(request.key)
        return database_pb2.ReadResponse(
            value=value or "",
            version=version,
            found=value is not None
        )

    def Write(self, request, context):
        # Placeholder — Julien will replace this with quorum logic
        success, version = self.store.local_write(
            request.key,
            request.value,
            request.expected_version
        )
        return database_pb2.WriteResponse(
            success=success,
            version=version,
            error="" if success else "Version mismatch"
        )

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    database_pb2_grpc.add_BooksDatabaseServicer_to_server(
        BooksDatabaseServicer(), server
    )
    server.add_insecure_port('[::]:50055')
    server.start()
    print("Books Database running on port 50055")
    server.wait_for_termination()

if __name__ == '__main__':
    serve()