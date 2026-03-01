import sys
import os

FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
suggestions_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/suggestions'))
sys.path.insert(0, suggestions_grpc_path)
import suggestions_pb2 as suggestions
import suggestions_pb2_grpc as suggestions_grpc

import grpc
from concurrent import futures

# Book catalogue with genres
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

# Build a flat lookup: title -> genre
TITLE_TO_GENRE = {}
for genre, books in CATALOGUE.items():
    for book in books:
        TITLE_TO_GENRE[book["title"]] = genre


class SuggestionsService(suggestions_grpc.SuggestionsServiceServicer):
    def GetSuggestions(self, request, context):
        cart_titles = set(request.book_titles)

        # Find genres of books in cart
        genres = set()
        for title in cart_titles:
            if title in TITLE_TO_GENRE:
                genres.add(TITLE_TO_GENRE[title])

        # Suggest books from the same genres, excluding cart items
        suggested = []
        for genre in genres:
            for book in CATALOGUE[genre]:
                if book["title"] not in cart_titles:
                    suggested.append(suggestions.Book(
                        title=book["title"],
                        author=book["author"]
                    ))

        return suggestions.SuggestionsResponse(books=suggested)


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    suggestions_grpc.add_SuggestionsServiceServicer_to_server(SuggestionsService(), server)
    server.add_insecure_port('[::]:50053')
    server.start()
    print("Suggestions service running on port 50053")
    server.wait_for_termination()


if __name__ == '__main__':
    serve()