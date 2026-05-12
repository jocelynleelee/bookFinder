"""
ingest_library.py
Fetches board-book data from BiblioCommons library APIs and persists it to the DB.
"""

import time
from datetime import datetime, timezone

import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, Book, LibraryHistory

# ──────────────────────────────────────────
# Config
# ──────────────────────────────────────────

# DATABASE_URL = "postgresql://postgres@localhost:5432/kidbookdb"
DATABASE_URL = "postgresql://kidbookdb_g3bi_user:NvS4CNu0BX2I1RIZrZOTHMMfqmC3AmQV@dpg-d800jdpo3t8c73db4b8g-a/kidbookdb_g3bi"

LIBRARIES = {
    "VPL": "https://gateway.bibliocommons.com/v2/libraries/vpl/bibs/search"
            "?query=board+book&searchType=keyword&f_FORMAT=BOARD_BK&f_PRIMARY_LANGUAGE=eng",
    # "BPL": "https://gateway.bibliocommons.com/v2/libraries/burnaby/bibs/search?...",
}

BOOK_BASE_LINKS = {
    "VPL": "https://vpl.bibliocommons.com/v2/record/",
    # "BPL": "https://bpl.bibliocommons.com/v2/record/",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Referer": "https://vpl.bibliocommons.com/",
}

REQUEST_TIMEOUT = 10   # seconds
PAGE_DELAY = 3         # seconds between pages

# ──────────────────────────────────────────
# DB setup
# ──────────────────────────────────────────

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
Session = sessionmaker(bind=engine)
Base.metadata.create_all(engine)

# ──────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────

def extract_isbn(isbn_list):
    """Return the first ISBN from a list, or None."""
    if isinstance(isbn_list, list) and isbn_list:
        return isbn_list[0]
    return None


def parse_book_item(book_id, book_info):
    """Parse a single bib entry from the API response into a flat dict."""
    brief = book_info.get("briefInfo", {})
    availability = book_info.get("availability", {})

    if availability.get("availableCopies") is None or availability.get("totalCopies") is None:
        return None

    return {
        "id": book_id,
        "title": brief.get("title"),
        "authors": " ".join(brief.get("authors", [])),
        "format": brief.get("format"),
        "isbn": brief.get("isbns", []),
        "publication_year": brief.get("publicationDate"),
        "image": brief.get("jacket", {}).get("medium"),
        "availability": {
            "status": availability.get("status"),
            "available_copies": availability.get("availableCopies"),
            "total_copies": availability.get("totalCopies"),
            "held_copies": availability.get("heldCopies"),
        },
        "audiences": brief.get("audiences", []),
        "rating": brief.get("rating", {}),
        "compositeSubjectHeadings": brief.get("compositeSubjectHeadings", []),
    }


# ──────────────────────────────────────────
# DB persistence
# ──────────────────────────────────────────

def save_page_to_db(session, library_name, items):
    """Upsert books and insert a new LibraryHistory row for each item."""
    for item in items:
        isbn = extract_isbn(item.get("isbn"))
        if not isbn:
            continue

        # Upsert book
        book = session.get(Book, isbn)
        if not book:
            book = Book(
                isbn=isbn,
                title=item.get("title"),
                author=item.get("authors"),
                image_url=item.get("image"),
                format=item.get("format"),
                publication_year=item.get("publication_year"),
                tags=[],
                rating_average=item.get("rating", {}).get("averageRating"),
                rating_count=item.get("rating", {}).get("totalCount"),
                audiences=item.get("audiences", []),
                composite_subjects=item.get("compositeSubjectHeadings", []),
            )
            session.add(book)

        # Insert history (timestamp is set at call time so duplicates within the
        # same second are unlikely; a daily dedup constraint is preferred at the
        # DB level for production use)
        availability = item.get("availability", {})
        history = LibraryHistory(
            isbn=isbn,
            book_id=item.get("id"),
            library=library_name,
            timestamp=datetime.now(timezone.utc),
            available_copies=availability.get("available_copies"),
            total_copies=availability.get("total_copies"),
            held_copies=availability.get("held_copies"),
            status=availability.get("status"),
            link=BOOK_BASE_LINKS[library_name] + item["id"],
        )
        session.add(history)

    session.commit()


# ──────────────────────────────────────────
# Fetching
# ──────────────────────────────────────────

def fetch_library_books(library_name, base_url):
    """Page through the BiblioCommons search API and persist each page."""
    session = Session()
    page = 1

    try:
        while True:
            url = f"{base_url}&page={page + 1}"
            print(f"🔍 {library_name} — page {page + 1}")

            try:
                response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
            except requests.RequestException as e:
                print(f"❌ Request failed: {e}")
                break

            bibs = response.json().get("entities", {}).get("bibs", {})
            if not bibs:
                print(f"✅ {library_name} — no more results at page {page + 1}")
                break

            page_items = []
            for book_id, book_info in bibs.items():
                parsed = parse_book_item(book_id, book_info)
                if parsed:
                    page_items.append(parsed)

            save_page_to_db(session, library_name, page_items)
            print(f"   Saved {len(page_items)} books")

            page += 1
            time.sleep(PAGE_DELAY)
    finally:
        session.close()


# ──────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────

def run_pipeline():
    for lib_name, lib_url in LIBRARIES.items():
        print(f"\n🚀 Processing {lib_name}…")
        fetch_library_books(lib_name, lib_url)


if __name__ == "__main__":
    run_pipeline()
