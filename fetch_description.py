"""
fetch_descriptions.py

Fetches from OpenLibrary for each book:
  - description
  - number_of_pages
  - subjects (merged into the book's tags column)

Only processes books missing a description (safe to re-run).

Usage:
    python fetch_descriptions.py
"""

import json
import time

import requests
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# ──────────────────────────────────────────
# Config
# ──────────────────────────────────────────

DATABASE_URL = "postgresql://postgres@localhost:5432/kidbookdb"
REQUEST_DELAY = 1.0  # seconds between books — be polite to OpenLibrary
REQUEST_TIMEOUT = 10

# ──────────────────────────────────────────
# DB setup
# ──────────────────────────────────────────

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
Session = sessionmaker(bind=engine)

# ──────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────

def extract_description(data):
    """Pull description string from an edition or work record."""
    desc = data.get("description")
    if not desc:
        return None
    if isinstance(desc, dict):
        return desc.get("value", "").strip() or None
    return str(desc).strip() or None


def extract_subjects(data):
    """
    Pull subject names from an edition or work record.
    OpenLibrary subjects can be strings or dicts with a 'name' key.
    """
    subjects = []
    for s in data.get("subjects", []):
        if isinstance(s, str):
            subjects.append(s.strip())
        elif isinstance(s, dict) and s.get("name"):
            subjects.append(s["name"].strip())
    return subjects


def fetch_openlibrary(isbn):
    """
    Fetch description, number_of_pages, and subjects for a given ISBN.
    Returns a dict with keys: description, number_of_pages, subjects.
    """
    result = {"description": None, "number_of_pages": None, "subjects": []}

    url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data"
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json().get(f"ISBN:{isbn}")
    except requests.RequestException as e:
        print(f"  ⚠️  Request failed: {e}")
        return result

    if not data:
        return result

    result["description"] = extract_description(data)
    result["number_of_pages"] = data.get("number_of_pages")
    result["subjects"] = extract_subjects(data)

    # If description or subjects are missing, follow the work record
    works = data.get("works", [])
    if works and (not result["description"] or not result["subjects"]):
        work_key = works[0].get("key")
        if work_key:
            try:
                work_resp = requests.get(
                    f"https://openlibrary.org{work_key}.json",
                    timeout=REQUEST_TIMEOUT,
                )
                work_resp.raise_for_status()
                work_data = work_resp.json()

                if not result["description"]:
                    result["description"] = extract_description(work_data)
                if not result["subjects"]:
                    result["subjects"] = extract_subjects(work_data)

            except requests.RequestException as e:
                print(f"  ⚠️  Work request failed: {e}")

    return result


def merge_tags(existing_tags, new_subjects):
    """Merge new subjects into existing tags, deduplicating case-insensitively."""
    existing_lower = {t.lower() for t in (existing_tags or [])}
    merged = list(existing_tags or [])
    for subject in new_subjects:
        if subject.lower() not in existing_lower:
            merged.append(subject)
            existing_lower.add(subject.lower())
    return merged


# ──────────────────────────────────────────
# Main pipeline
# ──────────────────────────────────────────

def run():
    session = Session()

    try:
        # Fetch books missing a description; also get current tags to merge into
        rows = session.execute(
            text("SELECT isbn, tags FROM books WHERE description IS NULL ORDER BY isbn")
        ).fetchall()

        total = len(rows)
        print(f"📚 Found {total} books without descriptions\n")

        found = 0
        for i, (isbn, existing_tags) in enumerate(rows, 1):
            print(f"[{i}/{total}] {isbn}", end=" ... ")

            ol = fetch_openlibrary(isbn)

            if not ol["description"] and not ol["subjects"] and not ol["number_of_pages"]:
                print("— nothing found")
                time.sleep(REQUEST_DELAY)
                continue

            merged_tags = merge_tags(existing_tags, ol["subjects"])

            session.execute(
                text("""
                    UPDATE books
                    SET description     = COALESCE(:desc, description),
                        number_of_pages = COALESCE(:pages, number_of_pages),
                        tags            = :tags
                    WHERE isbn = :isbn
                """),
                {
                    "desc": ol["description"],
                    "pages": ol["number_of_pages"],
                    "tags": json.dumps(merged_tags),
                    "isbn": isbn,
                },
            )
            session.commit()

            parts = []
            if ol["description"]:
                parts.append(f"description ({len(ol['description'])} chars)")
            if ol["number_of_pages"]:
                parts.append(f"{ol['number_of_pages']} pages")
            if ol["subjects"]:
                parts.append(f"{len(ol['subjects'])} subjects")
            print(f"✅ {', '.join(parts)}")
            found += 1

            time.sleep(REQUEST_DELAY)

    finally:
        session.close()

    print(f"\n✅ Done — updated {found}/{total} books")


if __name__ == "__main__":
    run()
