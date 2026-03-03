import os
import sys
import grpc
from concurrent import futures

# Make sure imports resolve when running in Docker with mounted utils
FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
suggestions_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/suggestions'))
sys.path.insert(0, suggestions_grpc_path)
import suggestions_pb2 as suggestions
import suggestions_pb2_grpc as suggestions_grpc


class SuggestionserviceServicer(suggestions_grpc.SuggestionserviceServicer):
    """Simple suggestions service: return sample books based on item keywords."""
    def GetSuggestions(self, request, context):
        items = list(request.items)

        # Very small rule-based suggestion engine: map keyword -> book
        mapping = {
            'python': {'bookId': 'bk-py-1', 'title': 'Learning Python', 'author': 'Author A'},
            'data': {'bookId': 'bk-ds-1', 'title': 'Data Science Handbook', 'author': 'Author B'},
            'machine': {'bookId': 'bk-ml-1', 'title': 'Intro to Machine Learning', 'author': 'Author C'},
            'romance': {'bookId': 'bk-rom-1', 'title': 'A Romantic Tale', 'author': 'Author D'},
            'fantasy': {'bookId': 'bk-fan-1', 'title': 'The Great Fantasy', 'author': 'Author E'},
        }

        suggested = []
        seen = set()
        for it in items:
            # naive lower/contains matching
            key = it.lower()
            for k, v in mapping.items():
                if k in key and v['bookId'] not in seen:
                    seen.add(v['bookId'])
                    suggested.append(suggestions.SuggestedBook(bookId=v['bookId'], title=v['title'], author=v['author']))

        # Fallback: if no matches, return some generic picks
        if not suggested:
            suggested = [
                suggestions.SuggestedBook(bookId='bk-001', title='The Best Book', author='Author 1'),
                suggestions.SuggestedBook(bookId='bk-002', title='Another Good Read', author='Author 2'),
            ]

        return suggestions.SuggestionResponse(suggestedBooks=suggested)


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    suggestions_grpc.add_SuggestionserviceServicer_to_server(SuggestionserviceServicer(), server)
    server.add_insecure_port('[::]:50053')
    server.start()
    print('Suggestions gRPC service running on 0.0.0.0:50053')
    server.wait_for_termination()


if __name__ == '__main__':
    serve()
