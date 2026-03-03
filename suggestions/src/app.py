import sys
import os

# This set of lines are needed to import the gRPC stubs.
# The path of the stubs is relative to the current file, or absolute inside the container.
# Change these lines only if strictly needed.
FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
suggestions_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/suggestions'))
sys.path.insert(0, suggestions_grpc_path)
import suggestions_pb2 as suggestions
import suggestions_pb2_grpc as suggestions_grpc

import grpc
from concurrent import futures

# Create a class to define the server functions, derived from
# suggestions_pb2_grpc.SuggestionsserviceServicer (generated from .proto)

class SuggestionsService(suggestions_grpc.SuggestionsserviceServicer):
    # simple in‑memory mapping from purchased product to book recommendations
    BOOK_RECOMMENDATIONS = {
        # product -> recommended titles
        "laptop": ["The Pragmatic Programmer", "Clean Code"],
        "smartphone": ["Smartphone Photography for Beginners", "Mobile UX Design"],
        "headphones": ["Sound Engineering 101", "The Musician's Guide to Acoustics"],
        # keywords that might appear in a cart item
        "python": ["Learning Python", "Fluent Python"],
        "data": ["Data Science from Scratch", "Hands-On Machine Learning"],
    }

    def _generate_suggestions(self, items):
        
        suggestions_set = set()

        for raw in items:
            token = raw.strip().lower()
            # try exact match or look for keyword substrings
            if token in self.BOOK_RECOMMENDATIONS:
                suggestions_set.update(self.BOOK_RECOMMENDATIONS[token])
            else:
                for key, titles in self.BOOK_RECOMMENDATIONS.items():
                    if key in token:
                        suggestions_set.update(titles)

        # if we didn't find anything, return a few generic bestsellers
        if not suggestions_set:
            suggestions_set.update(["Bestseller: A Good Read", "Classics for Everyone"])

        # limit to three suggestions to keep response small
        return list(suggestions_set)[:3]

    # Create an RPC function to suggest
    def GetSuggestions(self, request, context):
        print(f"Received request - Items: {request.items}")
        response = suggestions.SuggestionsResponse()
        # field name changed after regeneration
        response.suggested_books.extend(self._generate_suggestions(request.items))
        return response

def serve():
    server = grpc.server(futures.ThreadPoolExecutor())
    suggestions_grpc.add_SuggestionsserviceServicer_to_server(SuggestionsService(), server)
    
    port = "50053"
    server.add_insecure_port("[::]:" + port)
    server.start()
    print("Suggestions server started. Listening on port 50053.")
    server.wait_for_termination()

if __name__ == '__main__':
    serve()