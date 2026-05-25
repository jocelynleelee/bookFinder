import json
import feedparser
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import Counter
from contextlib import contextmanager

from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from models import Book, LibraryHistory
# from childcare_routes import register_childcare_routes
from vch_routes import register_vch_routes
from transit_routes import register_translink_routes

# ──────────────────────────────────────────
# App & DB setup
# ──────────────────────────────────────────

# DATABASE_URL = "postgresql://postgres@localhost:5432/kidbookdb"
DATABASE_URL = "postgresql://kidbookdb_g3bi_user:NvS4CNu0BX2I1RIZrZOTHMMfqmC3AmQV@dpg-d800jdpo3t8c73db4b8g-a/kidbookdb_g3bi"
app = Flask(__name__)
CORS(app)
register_vch_routes(app)
register_translink_routes(app)
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
Session = sessionmaker(bind=engine)

with open("family_places.json", "r", encoding="utf-8") as f:
    family_places = json.load(f)

VANCOUVER_COMMUNITY_CENTRES = {
    "hillcrest community centre": (49.2432, -123.1076),
    "trout lake community centre": (49.2540, -123.0650),
    "kerrisdale community centre": (49.2345, -123.1554),
    "roundhouse community arts & recreation centre": (49.2734, -123.1217),
    "douglas park community centre": (49.2497, -123.1207),
    "britannia community centre": (49.2775, -123.0715),
    "killarney community centre": (49.2205, -123.0388),
    "kitsilano community centre": (49.2680, -123.1686),
    "dunbar community centre": (49.2496, -123.1857),
    "sunset community centre": (49.2251, -123.0905),
    "creekside community recreation centre": (49.2710, -123.1048),
    "false creek community centre": (49.2694, -123.1151),
    "coal harbour community centre": (49.2920, -123.1297),
    "west end community centre": (49.2829, -123.1335),
    "mount pleasant community centre": (49.2626, -123.1000),
    "marpole-oakridge community centre": (49.2108, -123.1286),
    "oakridge centre community centre": (49.2334, -123.1162),
    "renfrew park community centre": (49.2455, -123.0434),
    "thunderbird community centre": (49.2732, -123.0550),
    "champlain heights community centre": (49.2156, -123.0315),
    "raycam community centre": (49.2803, -123.0873),
    "strathcona community centre": (49.2785, -123.0901),
    "hastings community centre": (49.2817, -123.0389),
    "japanese hall community centre": (49.2808, -123.0482),
    "gathering place community centre": (49.2789, -123.1280),
    "evelyne saller centre": (49.2815, -123.1030),
    "west point grey community centre": (49.2687, -123.2098),
    "kensington community centre": (49.2384, -123.0742),
    "cedar cottage neighbourhood house": (49.2492, -123.0737),
    "collingwood neighbourhood house": (49.2362, -123.0518),
}


def format_event(start_iso, end_iso):
    start = datetime.fromisoformat(start_iso)
    end = datetime.fromisoformat(end_iso)

    display_date = start.strftime("%b %d, %Y")  # May 25, 2026
    display_time = f"{start.strftime('%I:%M %p')} - {end.strftime('%I:%M %p')}"

    return display_date, display_time


@contextmanager
def get_session():
    """Yield a session and always close it, rolling back on error."""
    session = Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ──────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────

FILTER_TAGS = {
    "board books", "board book", "fiction", "nonfiction", "non-fiction",
    "juvenile fiction", "juvenile nonfiction", "juvenile literature",
    "children's fiction", "children's nonfiction", "picture book", "picture books",
    "stories in rhyme", "toy and movable books", "novelty books",
    "infants and toddlers", "babies", "children", "toddlers",
    "english language", "english", "language arts",
}

FILTER_PREFIXES = {
    "bilingual materials",
}

def get_top_tags(tag_lists, limit=20):
    counter = Counter(
        tag for tags in tag_lists
        for tag in (tags or [])
        if tag.lower().strip() not in FILTER_TAGS
        and len(tag.strip()) > 2
        and not any(tag.lower().strip().startswith(p) for p in FILTER_PREFIXES)
    )
    return [tag for tag, _ in counter.most_common(limit)]


# ──────────────────────────────────────────
# Page routes
# ──────────────────────────────────────────

@app.route("/")
def home():
    with get_session() as session:
        rows = session.query(Book.composite_subjects).all()
        all_tags = []
        for row in rows:
            # Split "Birds -- Juvenile Fiction" into ["Birds", "Juvenile Fiction"]
            for subject in (row.composite_subjects or []):
                parts = [p.strip() for p in subject.split("--")]
                all_tags.append(parts)
    top_tags = get_top_tags(all_tags, limit=20)
    return render_template("index.html", top_tags=top_tags)


@app.route("/bookdeals")
def book_deals_page():
    return render_template("bookoutlet.html")


@app.route("/availability-page")
def availability_page():
    return render_template("availability.html")


@app.route("/trending")
def trending_page():
    return render_template("trending.html")


@app.route("/book/<book_id>")
def book_detail_page(book_id):
    return render_template("book_detail.html", book_id=book_id)


@app.route("/outlet-history-page")
def outlet_history_page():
    return render_template("outlet_history.html")

@app.route("/schedule")
def show_schedule():
    return render_template("schedule.html")

# ──────────────────────────────────────────
# API routes
# ──────────────────────────────────────────

@app.route("/books")
def get_books():
    page = request.args.get("page", 1, type=int)
    limit = request.args.get("limit", 48, type=int)
    offset = (page - 1) * limit
    tags = request.args.get("tags", "", type=str)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    available_only = request.args.get("available", "false").lower() == "true"

    available_only = request.args.get("available", "false").lower() == "true"

    # Subquery to get latest outlet price per isbn
    outlet_join = """
        LEFT JOIN (
            SELECT DISTINCT ON (isbn) isbn, price
            FROM outlet_history
            ORDER BY isbn, timestamp DESC
        ) oh ON oh.isbn = b.isbn
    """

    if tag_list:
        tag_conditions = " AND ".join([
            f"EXISTS ("
            f"SELECT 1 FROM jsonb_array_elements_text(b.composite_subjects::jsonb) subj, "
            f"unnest(string_to_array(subj, ' -- ')) part "
            f"WHERE LOWER(TRIM(part)) = LOWER(:{f'tag{i}'})"
            f")"
            for i in range(len(tag_list))
        ])
        available_clause = "AND lh.available_copies > 0" if available_only else ""
        query = text(f"""
            SELECT DISTINCT ON (b.isbn)
                b.isbn,
                b.title,
                lh.book_id,
                b.image_url,
                lh.link,
                oh.price AS outlet_price
            FROM books b
            LEFT JOIN library_history lh ON b.isbn = lh.isbn
            {outlet_join}
            WHERE {tag_conditions} {available_clause}
            ORDER BY b.isbn, lh.timestamp DESC
            LIMIT :limit OFFSET :offset
        """)
        count_query = text(f"""
            SELECT COUNT(DISTINCT b.isbn)
            FROM books b
            LEFT JOIN library_history lh ON b.isbn = lh.isbn
            WHERE {tag_conditions} {available_clause}
        """)
        params = {f"tag{i}": tag_list[i] for i in range(len(tag_list))}
        params["limit"] = limit
        params["offset"] = offset
        count_params = {f"tag{i}": tag_list[i] for i in range(len(tag_list))}
    else:
        available_clause = "AND lh.available_copies > 0" if available_only else ""
        query = text(f"""
            SELECT DISTINCT ON (lh.isbn)
                b.isbn,
                b.title,
                lh.book_id,
                b.image_url,
                lh.link,
                oh.price AS outlet_price
            FROM books b
            LEFT JOIN library_history lh ON b.isbn = lh.isbn
            {outlet_join}
            WHERE 1=1 {available_clause}
            ORDER BY lh.isbn, lh.timestamp DESC
            LIMIT :limit OFFSET :offset
        """)
        count_query = text(f"""
            SELECT COUNT(DISTINCT b.isbn)
            FROM books b
            LEFT JOIN library_history lh ON b.isbn = lh.isbn
            WHERE 1=1 {available_clause}
        """)
        params = {"limit": limit, "offset": offset}
        count_params = {}

    with engine.connect() as conn:
        rows = conn.execute(query, params).mappings().all()
        total = conn.execute(count_query, count_params).scalar()

    return jsonify({
        "books": [dict(r) for r in rows],
        "page": page,
        "limit": limit,
        "total": total,
        "has_more": offset + limit < total,
    })


@app.route("/search")
def search():
    q = request.args.get("q", "", type=str).strip()
    page = request.args.get("page", 1, type=int)
    limit = request.args.get("limit", 48, type=int)
    offset = (page - 1) * limit
    source = request.args.get("source", "library")  # "library" or "outlet"

    if not q:
        return jsonify({"books": [], "total": 0, "has_more": False})

    if source == "outlet":
        query = text("""
            SELECT DISTINCT ON (oh.isbn)
                b.isbn,
                b.title,
                b.image_url,
                b.author,
                oh.price,
                oh.original_price,
                oh.inventory
            FROM outlet_history oh
            JOIN books b ON b.isbn = oh.isbn
            WHERE b.title ILIKE :q OR b.author ILIKE :q
            ORDER BY oh.isbn, oh.timestamp DESC
            LIMIT :limit OFFSET :offset
        """)
        count_query = text("""
            SELECT COUNT(DISTINCT b.isbn)
            FROM outlet_history oh
            JOIN books b ON b.isbn = oh.isbn
            WHERE b.title ILIKE :q OR b.author ILIKE :q
        """)
    else:
        query = text("""
            SELECT DISTINCT ON (b.isbn)
                b.isbn,
                b.title,
                b.image_url,
                lh.link
            FROM books b
            LEFT JOIN library_history lh ON b.isbn = lh.isbn
            WHERE b.title ILIKE :q OR b.author ILIKE :q
            ORDER BY b.isbn, lh.timestamp DESC
            LIMIT :limit OFFSET :offset
        """)
        count_query = text("""
            SELECT COUNT(DISTINCT b.isbn)
            FROM books b
            WHERE b.title ILIKE :q OR b.author ILIKE :q
        """)

    params = {"q": f"%{q}%", "limit": limit, "offset": offset}
    count_params = {"q": f"%{q}%"}

    with engine.connect() as conn:
        rows = conn.execute(query, params).mappings().all()
        total = conn.execute(count_query, count_params).scalar()

    return jsonify({
        "books": [dict(r) for r in rows],
        "page": page,
        "limit": limit,
        "total": total,
        "has_more": offset + limit < total,
    })


@app.route("/bookoutlet")
def bookoutlet():
    page = request.args.get("page", 1, type=int)
    limit = request.args.get("limit", 48, type=int)
    offset = (page - 1) * limit

    query = text("""
        SELECT DISTINCT ON (oh.isbn)
            b.isbn,
            b.title,
            b.image_url,
            b.author,
            oh.price,
            oh.original_price,
            oh.inventory,
            lh.available_copies AS library_available
        FROM outlet_history oh
        JOIN books b ON b.isbn = oh.isbn
        LEFT JOIN (
            SELECT DISTINCT ON (isbn) isbn, available_copies
            FROM library_history
            ORDER BY isbn, timestamp DESC
        ) lh ON lh.isbn = b.isbn
        ORDER BY oh.isbn, oh.timestamp DESC
        LIMIT :limit OFFSET :offset
    """)
    count_query = text("SELECT COUNT(DISTINCT oh.isbn) FROM outlet_history oh")
    with engine.connect() as conn:
        rows = conn.execute(query, {"limit": limit, "offset": offset}).mappings().all()
        total = conn.execute(count_query).scalar()

    return jsonify({
        "books": [dict(r) for r in rows],
        "page": page,
        "limit": limit,
        "total": total,
        "has_more": offset + limit < total,
    })


@app.route("/availability-history/<book_id>")
def availability_history(book_id):
    with get_session() as session:
        rows = (
            session.query(
                LibraryHistory.timestamp,
                LibraryHistory.available_copies,
                LibraryHistory.total_copies,
                LibraryHistory.library,
            )
            .filter(LibraryHistory.book_id == book_id)
            .order_by(LibraryHistory.timestamp.asc())
            .limit(300)
            .all()
        )

    return jsonify([
        {
            "timestamp": r.timestamp.isoformat(),
            "available": r.available_copies,
            "total": r.total_copies,
            "library": r.library,
        }
        for r in rows
    ])


@app.route("/book_data/<book_id>")
def book_data(book_id):
    with get_session() as session:
        rows = (
            session.query(LibraryHistory)
            .filter(LibraryHistory.book_id == book_id)
            .order_by(LibraryHistory.timestamp.asc())
            .all()
        )

        if not rows:
            return jsonify({"error": "book_id not found"}), 404

        book = (
            session.query(
                Book.title, Book.image_url, Book.description,
                Book.number_of_pages, Book.composite_subjects, Book.author
            )
            .filter(Book.isbn == rows[0].isbn)
            .first()
        )

        isbn = rows[0].isbn

        # Check if this book is on BookOutlet
        outlet = session.execute(
            text("""
                SELECT price, original_price
                FROM outlet_history
                WHERE isbn = :isbn
                ORDER BY timestamp DESC
                LIMIT 1
            """),
            {"isbn": isbn}
        ).fetchone()

        book_info = {
            "title": book.title if book else "Unknown",
            "image": book.image_url if book else None,
            "isbn": isbn,
            "description": book.description if book else None,
            "number_of_pages": book.number_of_pages if book else None,
            "composite_subjects": book.composite_subjects if book else [],
            "author": book.author if book else None,
            "outlet_price": float(outlet.price) if outlet and outlet.price else None,
            "outlet_original_price": float(outlet.original_price) if outlet and outlet.original_price else None,
        }
        history = [
            {
                "timestamp": r.timestamp.isoformat(),
                "available": r.available_copies,
                "total": r.total_copies,
                "held": r.held_copies,
                "library": r.library,
                "link": r.link,
            }
            for r in rows
        ]

    return jsonify({"book": book_info, "history": history})


@app.route("/trending-books")
def trending_books():
    # Single query with JOIN — eliminates the N+1 from the original
    query = text("""
        WITH ranked AS (
            SELECT
                lh.book_id,
                lh.isbn,
                lh.library,
                lh.timestamp,
                lh.available_copies,
                LAG(lh.available_copies) OVER (
                    PARTITION BY lh.book_id, lh.library
                    ORDER BY lh.timestamp
                ) AS prev_available
            FROM library_history lh
            WHERE COALESCE(lh.total_copies, 0) > 0
        ),
        changes AS (
            SELECT
                book_id,
                isbn,
                (prev_available - available_copies) AS drop
            FROM ranked
            WHERE prev_available IS NOT NULL
        )
        SELECT
            c.book_id,
            c.isbn,
            SUM(c.drop) AS total_drop,
            b.title,
            b.image_url AS image
        FROM changes c
        JOIN books b ON b.isbn = c.isbn
        WHERE c.drop > 0
        GROUP BY c.book_id, c.isbn, b.title, b.image_url
        ORDER BY total_drop DESC
        LIMIT 24
    """)

    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()

    return jsonify([
        {
            "book_id": r["book_id"],
            "isbn": r["isbn"],
            "title": r["title"],
            "image": r["image"],
            "drop": int(r["total_drop"]),
        }
        for r in rows
    ])


@app.route("/trending-time")
def trending_time():
    query = text("""
        WITH ranked AS (
            SELECT
                lh.book_id,
                lh.isbn,
                lh.timestamp,
                (
                    (COALESCE(lh.held_copies, 0)::float / lh.total_copies) +
                    (1 - COALESCE(lh.available_copies, 0)::float / lh.total_copies)
                ) AS demand,
                LAG(
                    (
                        (COALESCE(lh.held_copies, 0)::float / lh.total_copies) +
                        (1 - COALESCE(lh.available_copies, 0)::float / lh.total_copies)
                    )
                ) OVER (
                    PARTITION BY lh.book_id
                    ORDER BY lh.timestamp
                ) AS prev_demand
            FROM library_history lh
            WHERE lh.total_copies > 0
        )
        SELECT
            r.book_id,
            b.isbn,
            b.title,
            b.image_url AS image,
            (r.demand - r.prev_demand) AS demand_change
        FROM ranked r
        JOIN books b ON b.isbn = r.isbn
        WHERE r.prev_demand IS NOT NULL
        ORDER BY demand_change DESC
        LIMIT 10
    """)

    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()

    return jsonify([
        {
            "book_id": r["book_id"],
            "title": r["title"],
            "image": r["image"],
            "change": float(r["demand_change"]),
        }
        for r in rows
    ])


@app.route("/outlet-history/<isbn>")
def outlet_history(isbn):
    query = text("""
        SELECT timestamp, price, original_price, inventory
        FROM outlet_history
        WHERE isbn = :isbn
        ORDER BY timestamp ASC
    """)

    with engine.connect() as conn:
        rows = conn.execute(query, {"isbn": isbn}).mappings().all()

    return jsonify([
        {
            "timestamp": r["timestamp"].isoformat() if r["timestamp"] else None,
            "price": float(r["price"]) if r["price"] is not None else None,
            "original_price": float(r["original_price"]) if r["original_price"] is not None else None,
            "inventory": r["inventory"],
        }
        for r in rows
    ])


@app.route("/admin/ingest-library", methods=["POST"])
def admin_ingest_library():
    """Manually trigger library ingestion"""
    from ingest_library import run_pipeline
    run_pipeline()
    return jsonify({"status": "Ingestion started"}), 200

@app.route("/admin/ingest-outlet", methods=["POST"])
def admin_ingest_outlet():
    """Manually trigger outlet ingestion"""
    from ingest_bookoutlet import run_ingest
    run_ingest()
    return jsonify({"status": "Ingestion started"}), 200

@app.route("/kid-events")
def kid_events_page():
    return render_template("kid_events.html")


@app.route("/api/kid-events")
def api_kid_events():
    events = []

    # -------------------------------------------------
    # Vancouver Public Library RSS
    # -------------------------------------------------

    rss_url = (
        "https://gateway.bibliocommons.com/v2/libraries/vpl/rss/events?audiences=53c940484246f6147c00000d%2C53c940484246f6147c00000e%2C53c940484246f6147c00000f"
    )

    feed = feedparser.parse(rss_url)

    for entry in feed.entries:
        location = getattr(entry, "bc_name", None)

        if not getattr(entry, "bc_latitude", None) or \
            not getattr(entry, "bc_longitude", None):
            continue
        start_time = getattr(entry, "bc_start_date_local", "")
        end_time = getattr(entry, "bc_end_date_local", "")
        display_date, display_time = format_event(
            start_time,
            end_time
        )
        
        events.append({
            "title": entry.title,
            "location": location,
            "lat": entry.bc_latitude,
            "lon": entry.bc_longitude,
            "display_date": display_date,
            "display_time": display_time,
            "summary": entry.get("summary", ""),
            "url": entry.link,
            "source": "library"
        })

    # -------------------------------------------------
    # Vancouver Community Centres
    # -------------------------------------------------

    url = "https://anc.ca.apm.activecommunities.com/vancouver/rest/activities/list"

    def fetch_page(page_number):
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://anc.ca.apm.activecommunities.com",
            "Referer": "https://anc.ca.apm.activecommunities.com/vancouver/activity/search",
            "Page_info": json.dumps({
                "order_by": "Name",
                "page_number": page_number,
                "total_records_per_page": 20
            })
        }

        payload = {
            "activity_search_pattern": {
                "activity_keyword": "",
                "activity_select_param": 2,
                "center_ids": [],
                "activity_category_ids": []
            },
            "activity_transfer_pattern": {}
        }

        r = requests.post(url, headers=headers, json=payload)

        if r.status_code != 200:
            return None

        return r.json()

    all_events = []

    for page in range(1, 10):
        data = fetch_page(page)

        if not data:
            break

        activities = data.get("body", {}).get("activity_items", [])

        if not activities:
            break

        all_events.extend(activities)

    for activity in all_events:
        location = activity.get("location", {}).get("label", "").strip("*").lower().replace("cmty", "community")

        coords = VANCOUVER_COMMUNITY_CENTRES.get(location)

        if not coords:
            continue
        date_range = activity.get("date_range")
        time_range_landing_page = activity.get("time_range_landing_page")
        fee_info = activity.get("fee")
        
        events.append({
            "title": activity.get("name"),
            "location": activity.get("location", {}).get("label"),
            "lat": coords[0],
            "lon": coords[1],
            "cta": fee_info,
            "display_date": date_range,
            "display_time": time_range_landing_page,
            "summary": activity.get("desc", ""),
            "url": activity.get("detail_url", ""),
            "age": activity.get("age_description"),
            "source": "community"
        })
    events.extend(family_places)
    return jsonify(events)

@app.route("/parks")
def parks():
    return render_template("parks-map.html")

@app.route("/transit")
def transit_page():
    return render_template("transit.html")

@app.route("/farms")
def farms():
    return render_template("farms-map.html")
# ──────────────────────────────────────────
# Error handlers
# ──────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "Internal server error"}), 500


# ──────────────────────────────────────────
# Run
# ──────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True)
