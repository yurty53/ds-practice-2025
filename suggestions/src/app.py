import sys
import os
import logging

FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
suggestions_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/suggestions'))
sys.path.insert(0, suggestions_grpc_path)
import suggestions_pb2 as suggestions
import suggestions_pb2_grpc as suggestions_grpc

import grpc
from concurrent import futures

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# Minimal initialization cache for order lifecycle alignment across services.
order_store = {}

# Vector clock store: order_id -> { service_name: int }
vector_clocks = {}

SERVICE_NAME = "suggestions"

# Book catalogue organized by genre
CATALOGUE = {
    "Magical Realism": [
        {"title": "100 Years of Solitude", "author": "Gabriel García Márquez"},
        {"title": "Feast of the Goat", "author": "Mario Vargas Llosa"},
        {"title": "The House of Spirits", "author": "Isabel Allende"},
    ],
    "Classic Literature": [
        {"title": "Brothers Karamazov", "author": "Fyodor Dostoevsky"},
        {"title": "Les Misérables", "author": "Victor Hugo"},
        {"title": "The Iliad", "author": "Homer"},
    ],
    "Fantasy": [
        {"title": "Lord of the Rings", "author": "J.R.R. Tolkien"},
        {"title": "The Name of the Wind", "author": "Patrick Rothfuss"},
        {"title": "The Chronicles of Narnia", "author": "C.S. Lewis"},
    ],
    "Sci-Fi": [
        {"title": "Dune", "author": "Frank Herbert"},
        {"title": "Neuromancer", "author": "William Gibson"},
        {"title": "Foundation", "author": "Isaac Asimov"},
    ],
    "Literary Fiction": [
        {"title": "Midnight's Children", "author": "Salman Rushdie"},
        {"title": "God of Small Things", "author": "Arundhati Roy"},
        {"title": "The Grapes of Wrath", "author": "John Steinbeck"},
    ],
}

# Build reverse mapping for O(1) genre lookup
TITLE_TO_GENRE = {}
for genre, books in CATALOGUE.items():
    for book in books:
        TITLE_TO_GENRE[book["title"]] = genre


class SuggestionsService(suggestions_grpc.SuggestionsServiceServicer):
    def _is_local_vc_inferior_or_equal(self, order_id, incoming_vc):
        local_vc = vector_clocks.get(order_id, {})
        for key, local_value in local_vc.items():
            if local_value > incoming_vc.get(key, 0):
                return False
        return True

    def InitOrder(self, request, context):
        order_id = request.order_id
        if not order_id:
            logger.warning("InitOrder rejected: missing order_id")
            return suggestions.InitOrderResponse(success=False)

        order_store[order_id] = {"initialized": True}
        vector_clocks[order_id] = {SERVICE_NAME: 0}
        logger.info(f"[{order_id}] Suggestions InitOrder complete")
        return suggestions.InitOrderResponse(success=True)

    def GetSuggestions(self, request, context):
        """
        Get book recommendations based on cart contents.
        Algorithm: find genres of cart books, suggest other books from same genres.
        """
        order_id = getattr(request, 'order_id', '')
        cart_titles = set(request.book_titles)

        # Merge incoming VC and increment own counter (event f)
        vc = dict(request.vector_clock)
        local_counter = vector_clocks.get(order_id, {}).get(SERVICE_NAME, 0)
        vc[SERVICE_NAME] = max(local_counter, vc.get(SERVICE_NAME, 0)) + 1
        if order_id in vector_clocks:
            vector_clocks[order_id] = vc

        logger.info(f"[{order_id}] Event f: GetSuggestions | cart: {list(cart_titles)} | VC: {vc}")

        # Identify genres of books in cart
        genres = set()
        for title in cart_titles:
            if title in TITLE_TO_GENRE:
                genres.add(TITLE_TO_GENRE[title])

        logger.info(f"[{order_id}] Detected genres: {genres}")

        # Find suggestions from same genres, excluding cart items
        suggested = []
        for genre in genres:
            for book in CATALOGUE[genre]:
                if book["title"] not in cart_titles:
                    suggested.append(suggestions.Book(
                        title=book["title"],
                        author=book["author"]
                    ))

        logger.info(f"[{order_id}] Returning {len(suggested)} suggestions")
        return suggestions.SuggestionsResponse(
            books=suggested,
            vector_clock=vc
        )

    def ClearOrder(self, request, context):
        order_id = request.order_id
        incoming_vc = dict(request.vector_clock)

        if order_id not in order_store or order_id not in vector_clocks:
            logger.info(f"[{order_id}] ClearOrder no-op: order not found")
            return suggestions.ClearResponse(success=True)

        local_vc = dict(vector_clocks[order_id])
        if self._is_local_vc_inferior_or_equal(order_id, incoming_vc):
            del order_store[order_id]
            del vector_clocks[order_id]
            logger.info(f"[{order_id}] ClearOrder applied | local_vc={local_vc} incoming_vc={incoming_vc}")
            return suggestions.ClearResponse(success=True)
        else:
            logger.warning(f"[{order_id}] ClearOrder rejected: local VC ahead of incoming | local_vc={local_vc} incoming_vc={incoming_vc}")
            return suggestions.ClearResponse(success=False)


def serve():
    """Start gRPC server on port 50053."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    suggestions_grpc.add_SuggestionsServiceServicer_to_server(SuggestionsService(), server)
    server.add_insecure_port('[::]:50053')
    server.start()
    logger.info("Suggestions service running on port 50053")
    server.wait_for_termination()


if __name__ == '__main__':
    serve()