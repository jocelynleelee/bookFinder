"""
ingest_bookoutlet.py
Searches the BookOutlet catalogue and saves prices/inventory to the DB.
"""

import json
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests
from sqlalchemy import create_engine, text

# ──────────────────────────────────────────
# Config
# ──────────────────────────────────────────

# DATABASE_URL = "postgresql+psycopg2://postgres@localhost:5432/kidbookdb"
DATABASE_URL = "postgresql://kidbookdb_g3bi_user:NvS4CNu0BX2I1RIZrZOTHMMfqmC3AmQV@dpg-d800jdpo3t8c73db4b8g-a/kidbookdb_g3bi"
SEARCH_URL = "https://ac.cnstrc.com/search/{query}"
API_KEY = "key_udjk7sSacp6D0sVq"
PAGE_DELAY = 5  # seconds between pages

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html,application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

KEYWORDS = [
    "priddy-books",
    "usborne-publishing-ltd",
    "carle-eric",
    "little-genius-books",
    "watt-fiona",
    "make-believe-ideas",
    "boynton-sandra",
]

# ──────────────────────────────────────────
# SQL statements
# ──────────────────────────────────────────

UPSERT_BOOK = text("""
    INSERT INTO books (isbn, title, author, image_url, tags)
    VALUES (:isbn, :title, :author, :image_url, :tags)
    ON CONFLICT (isbn) DO UPDATE SET
        title     = COALESCE(EXCLUDED.title,     books.title),
        author    = COALESCE(EXCLUDED.author,    books.author),
        image_url = COALESCE(EXCLUDED.image_url, books.image_url),
        tags      = COALESCE(EXCLUDED.tags,      books.tags)
""")

INSERT_HISTORY = text("""
    INSERT INTO outlet_history (isbn, timestamp, price, original_price, inventory)
    VALUES (:isbn, :timestamp, :price, :original_price, :inventory)
""")

# ──────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────

def normalize_isbn(raw_id):
    """Strip trailing 'B' that BookOutlet appends to some IDs."""
    if raw_id and raw_id.endswith("B"):
        return raw_id[:-1]
    return raw_id


engine = create_engine(DATABASE_URL, pool_pre_ping=True)


# ──────────────────────────────────────────
# Fetching
# ──────────────────────────────────────────

def search_bookoutlet(query, pages=5):
    """Return a list of product dicts for the given search query."""
    results = []

    for page in range(pages):
        url = f"{SEARCH_URL.format(query=quote(query))}?key={API_KEY}&page={page + 1}"

        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"❌ Request failed (page {page + 1}): {e}")
            break

        for item in resp.json().get("response", {}).get("results", []):
            d = item.get("data", {})
            results.append({
                "id": d.get("id"),
                "title": item.get("value"),
                "image_url": "https://images.bookoutlet.com/covers/" + (d.get("image_url") or ""),
                "original_price": d.get("list_price_cad"),
                "price": d.get("regular_price_cad"),
                "inventory": d.get("inventory", 0),
                "author": d.get("author_1", ""),
                "tags": d.get("tags", []),
            })

        time.sleep(PAGE_DELAY)

    return results


# ──────────────────────────────────────────
# DB persistence
# ──────────────────────────────────────────

def ingest_outlet_books(books):
    """Upsert each book and insert an outlet_history row."""
    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        for book in books:
            isbn = normalize_isbn(book.get("id"))
            if not isbn:
                continue

            conn.execute(UPSERT_BOOK, {
                "isbn": isbn,
                "title": book.get("title"),
                "author": book.get("author"),
                "image_url": book.get("image_url"),
                "tags": json.dumps(book.get("tags", [])),
            })

            conn.execute(INSERT_HISTORY, {
                "isbn": isbn,
                "timestamp": now,
                "price": book.get("price"),
                "original_price": book.get("original_price"),
                "inventory": book.get("inventory"),
            })


# ──────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────

def run_ingest():
    for keyword in KEYWORDS:
        print(f"\n🔍 Searching: {keyword}")
        books = search_bookoutlet(keyword, pages=5)
        print(f"   Found {len(books)} products")
        ingest_outlet_books(books)
        print(f"   ✅ Saved")


if __name__ == "__main__":
    run_ingest()
