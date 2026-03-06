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
# suggestions_pb2_grpc.SuggestionsServiceServicer (generated from .proto)

class SuggestionsService(suggestions_grpc.SuggestionsServiceServicer):
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

    # Mapping of authors for each book
    BOOK_AUTHORS = {
        "The Pragmatic Programmer": "David Thomas & Andrew Hunt",
        "Clean Code": "Robert C. Martin",
        "Smartphone Photography for Beginners": "John Miller",
        "Mobile UX Design": "Steven Hoober",
        "Sound Engineering 101": "Tom Holman",
        "The Musician's Guide to Acoustics": "Dave Hill",
        "Learning Python": "Mark Lutz",
        "Fluent Python": "Luciano Ramalho",
        "Data Science from Scratch": "Joel Grus",
        "Hands-On Machine Learning": "Aurélien Géron",
        "Bestseller: A Good Read": "Anonymous",
        "Classics for Everyone": "Classic Authors"
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
        suggested_titles = self._generate_suggestions(request.items)
        
        # Create Book objects with title and author
        for title in suggested_titles:
            book = response.suggested_books.add()
            book.title = title
            book.author = self.BOOK_AUTHORS.get(title, "Unknown Author")
        
        response.reason = f"Based on your interest in {', '.join(request.items[:2])}"
        return response

def serve():
    server = grpc.server(futures.ThreadPoolExecutor())
    suggestions_grpc.add_SuggestionsServiceServicer_to_server(SuggestionsService(), server)
    
    port = "50053"
    server.add_insecure_port("[::]:" + port)
    server.start()
    print("Suggestions server started. Listening on port 50053.")
    server.wait_for_termination()

if __name__ == '__main__':
    serve()