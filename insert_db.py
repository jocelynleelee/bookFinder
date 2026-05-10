import json
from datetime import datetime, timezone
from models import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Book, LibraryHistory, OutletHistory

# ------------------------
# DB setup
# ------------------------
DATABASE_URL = "postgresql://postgres@localhost:5432/kidbookdb"
BOOK_BASE_LINKS = {
    "VPL": "https://vpl.bibliocommons.com/v2/record/",
    "BPL": "https://bpl.bibliocommons.com/v2/record/",
    "RPL": "https://yourlibrary.bibliocommons.com/v2/record/"
}
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()
Base.metadata.create_all(engine)

# ------------------------
# Helpers
# ------------------------

def get_today():
    return datetime.utcnow().date()


def extract_isbn(isbn_field):
    """
    Handles:
    - ["9781339045634", "..."]
    - "9781339045634B"
    - plain string
    """
    if isinstance(isbn_field, list) and \
        len(isbn_field) > 0:
        isbn = isbn_field[0]
    else:
        isbn = isbn_field

    return isbn.replace("B", "").strip()


def extract_library(url: str) -> str:
    url = url.lower()

    if "vpl" in url:
        return "VPL"
    elif "bpl" in url:
        return "BPL"
    elif "rpl" in url:
        return "RPL"
    else:
        return "UNKNOWN"


def get_or_create_book(isbn, book_id=None, title=None, author=None, image_url=None, fmt=None):
    book = session.query(Book).filter_by(isbn=isbn).first()

    if not book:
        book = Book(
            isbn=isbn,
            book_id=book_id,
            title=title,
            author=author,
            image_url=image_url,
            format=fmt
        )
        session.add(book)
        session.flush()

    return book


# ------------------------
# LIBRARY INGESTION
# ------------------------
def ingest_library_jsonl(path):
    today = get_today()

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            data = json.loads(line)

            if not data.get("isbn"):
                continue
            isbn = extract_isbn(data.get("isbn"))

            # skip if already exists today (dedup)
            exists = session.query(LibraryHistory).filter_by(
                isbn=isbn,
                date=today,
                library=extract_library(data["link"])
            ).first()

            if exists:
                continue

            get_or_create_book(
                isbn=isbn,
                book_id=data.get("id"),
                title=data.get("title"),
                author=data.get("authors"),
                image_url=data.get("image")
            )
            link = data["link"]
            record = LibraryHistory(
                isbn=isbn,
                library=extract_library(data["link"]),
                timestamp=datetime.now(timezone.utc),
                available_copies=data["availability"]["available_copies"],
                total_copies=data["availability"]["total_copies"],
                status=data["availability"]["status"],
                link=link
            )

            session.add(record)

    session.commit()
    print("✅ Library ingestion complete")


# ------------------------
# BOOKOUTLET INGESTION
# ------------------------
def ingest_outlet_jsonl(path):
    today = get_today()

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            data = json.loads(line)

            isbn = extract_isbn(data.get("id"))

            exists = session.query(OutletHistory).filter_by(
                isbn=isbn,
                date=today
            ).first()

            if exists:
                continue

            get_or_create_book(
                isbn=isbn,
                title=data.get("title"),
                author=data.get("author"),
                image_url=data.get("image_url")
            )

            record = OutletHistory(
                isbn=isbn,
                date=today,
                price=data.get("price"),
                original_price=data.get("original_price"),
                inventory=data.get("inventory")
            )

            session.add(record)

    session.commit()
    print("✅ BookOutlet ingestion complete")


# ------------------------
# MAIN RUN
# ------------------------
if __name__ == "__main__":
    ingest_library_jsonl("all_cleaned2.jsonl")
    ingest_outlet_jsonl("board_book_deals2.jsonl")

    session.close()
    print("🚀 All ingestion done")