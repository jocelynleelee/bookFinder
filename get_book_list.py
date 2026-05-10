"""
ingest_vpl_list.py

Fetches a VPL curated list from the page's JSON-LD schema and injects the books
into your database with a curated list tag.

Usage:
    python ingest_vpl_list.py <list_url> <list_name>

Example:
    python ingest_vpl_list.py \
        "https://vpl.bibliocommons.com/v2/list/display/568159387/3004692517" \
        "VPL Staff Picks - Babies"
"""

import json
import re
import sys
from urllib.parse import urljoin

import requests
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# ──────────────────────────────────────────
# Config
# ──────────────────────────────────────────

DATABASE_URL = "postgresql://postgres@localhost:5432/kidbookdb"

# ──────────────────────────────────────────
# DB setup
# ──────────────────────────────────────────

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
Session = sessionmaker(bind=engine)

# ──────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────

def extract_isbn_from_url(image_url):
    """Extract ISBN from Syndetics image URL. Example:
    https://www.syndetics.com/index.aspx?isbn=9780375834011&...
    """
    match = re.search(r'isbn=(\d+)', image_url)
    return match.group(1) if match else None


def fetch_list_json(url):
    """Fetch the HTML page and extract JSON-LD data from script tags."""
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"❌ Failed to fetch {url}: {e}")
        return None

    # Extract all JSON-LD scripts
    pattern = r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>'
    matches = re.findall(pattern, resp.text, re.DOTALL)

    if not matches:
        print("❌ No JSON-LD scripts found on page")
        return None

    # Find the one with itemListElement
    for script_content in matches:
        try:
            data = json.loads(script_content)
            if isinstance(data, dict) and "@graph" in data:
                # It's a graph, find the ItemList
                for item in data.get("@graph", []):
                    if item.get("@type") == "ItemList" and "itemListElement" in item:
                        return item.get("itemListElement", [])
            elif data.get("@type") == "ItemList" and "itemListElement" in data:
                return data.get("itemListElement", [])
        except json.JSONDecodeError:
            continue

    print("❌ No ItemList found in JSON-LD scripts")
    return None


def ingest_list(items, list_name):
    """Upsert each book from the list into the database."""
    session = Session()
    added = 0
    skipped = 0

    try:
        for item in items:
            title = item.get("name", "").strip()
            if not title:
                skipped += 1
                continue

            image_url = item.get("image", "").strip()
            isbn = extract_isbn_from_url(image_url) if image_url else None

            if not isbn:
                print(f"⚠️  {title} — no ISBN found")
                skipped += 1
                continue

            description = item.get("description", "").strip()
            # Decode HTML entities if present
            description = (
                description
                .replace("&#34;", '"')
                .replace("&#39;", "'")
                .replace("&quot;", '"')
                .replace("&amp;", "&")
            )

            # Fetch existing tags for this ISBN
            existing = session.execute(
                text("SELECT tags FROM books WHERE isbn = :isbn"),
                {"isbn": isbn}
            ).fetchone()

            existing_tags = []
            if existing and existing[0]:
                try:
                    existing_tags = json.loads(existing[0]) if isinstance(existing[0], str) else existing[0]
                except (json.JSONDecodeError, TypeError):
                    existing_tags = []

            # Merge tags (deduplicate)
            merged_tags = list(set(existing_tags + [list_name]))

            # Upsert book
            session.execute(
                text("""
                    INSERT INTO books (isbn, title, image_url, description, tags)
                    VALUES (:isbn, :title, :image_url, :description, :tags)
                    ON CONFLICT (isbn) DO UPDATE SET
                        title       = COALESCE(EXCLUDED.title, books.title),
                        image_url   = COALESCE(EXCLUDED.image_url, books.image_url),
                        description = COALESCE(EXCLUDED.description, books.description),
                        tags        = EXCLUDED.tags
                """),
                {
                    "isbn": isbn,
                    "title": title,
                    "image_url": image_url,
                    "description": description,
                    "tags": json.dumps(merged_tags),
                },
            )
            session.commit()
            print(f"✅ {title}")
            added += 1

    finally:
        session.close()

    print(
        f"\n✅ Done — added {added} books, skipped {skipped}"
    )


# ──────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────

def main():
    if len(sys.argv) < 3:
        print("Usage: python ingest_vpl_list.py <list_url> <list_name>")
        print()
        print("Example:")
        print(
            '  python ingest_vpl_list.py '
            '"https://vpl.bibliocommons.com/v2/list/display/568159387/3004692517" '
            '"VPL Staff Picks - Babies"'
        )
        sys.exit(1)

    list_url = sys.argv[1]
    list_name = sys.argv[2]

    print(f"📚 Fetching list from {list_url}...")
    items = fetch_list_json(list_url)

    if not items:
        sys.exit(1)

    print(f"🔍 Found {len(items)} items\n")
    ingest_list(items, list_name)


if __name__ == "__main__":
    main()
