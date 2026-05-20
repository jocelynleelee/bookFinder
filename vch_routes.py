# vch_routes.py
# Add to your existing app.py:
#
#   from vch_routes import register_vch_routes
#   register_vch_routes(app)
#
# Requirements:
#   pip install requests flask-caching
#
# This proxies VCH API queries server-side (avoids CORS) and adds
# simple in-memory caching so repeated identical queries don't
# hammer the VCH API.

import requests
from flask import Flask, jsonify, request, render_template
from datetime import datetime

# ─── VCH API config ───────────────────────────────────────────────────────────

VCH_FACILITIES_URL    = "https://inspections.vch.ca/api/v0/portal/disclosure/program/facilities"
VCH_BASE_URL          = "https://inspections.vch.ca/api/v0/portal/disclosure/program"

PROGRAM_IDS = {
    "restaurants": "bfecaebb-8c76-43aa-be0d-6a00092ac5f1",
    "childcare":   "6e0e9442-3016-4294-83f4-0ea25b22ec5b",
}

RESTAURANT_FIELDS = [
    "community", "facilityName", "facilityType", "phoneNumber",
    "siteAddress", "latitude", "longitude", "emailAddress",
    "website", "lastInspectionDate", "hazardRating", "hazardScore",
    "closure", "operationsType", "totalInfractions",
    "outstandingCriticalInfractions", "outstandingNonCriticalInfractions",
]

CHILDCARE_FIELDS = [
    "community", "facilityName", "facilityType", "phoneNumber",
    "siteAddress", "latitude", "longitude", "emailAddress",
    "website", "lastInspectionDate", "hazardRating",
    "closure", "capacity", "totalInfractions",
    "outstandingCriticalInfractions", "outstandingNonCriticalInfractions",
]

VCH_HEADERS = {
    "Content-Type": "application/json",
    "Accept":       "application/json",
    "Origin":       "https://inspections.vch.ca",
    "Referer":      "https://inspections.vch.ca/",
    "User-Agent":   "Mozilla/5.0",
}

# ─── Simple in-memory cache ───────────────────────────────────────────────────
# Caches responses for 1 hour so repeated identical queries are instant.
# For production, replace with Redis or Flask-Caching.

import hashlib, time as _time
_cache = {}
# Cache TTL by data type
# Facilities change rarely — cache for 24 hours
# Inspections change even less — cache for 48 hours
CACHE_TTL_FACILITIES  = 86400   # 24 hours
CACHE_TTL_INSPECTIONS = 172800  # 48 hours
CACHE_TTL = CACHE_TTL_FACILITIES  # default

def _cache_key(*args, **kwargs):
    raw = str(args) + str(sorted(kwargs.items()))
    return hashlib.md5(raw.encode()).hexdigest()

def _cache_get(key, ttl=None):
    entry = _cache.get(key)
    ttl   = ttl or CACHE_TTL
    if entry and (_time.time() - entry["ts"]) < ttl:
        return entry["data"]
    return None

def _cache_set(key, data):
    _cache[key] = {"data": data, "ts": _time.time()}


# ─── VCH query helpers ────────────────────────────────────────────────────────

def query_vch_facilities(
    program: str,
    criteria: str = "",
    community: str = "",
    page: int = 0,
    page_size: int = 100,
    fields: list = None,
) -> dict:
    """
    Query VCH facilities API with optional filters.
    Returns the raw VCH response dict.
    """
    program_id = PROGRAM_IDS.get(program)
    if not program_id:
        raise ValueError(f"Unknown program: {program}")

    # VCH API filter structure — uses matchMode + values array
    filters = []
    if community:
        filters.append({
            "field":     "community",
            "matchMode": "in",
            "values":    [community],
        })

    payload = {
        "pageNumber":          page,
        "pageSize":            page_size,
        "criteria":            criteria,
        "sort":                [{"field": "community", "order": "asc"}],
        "disclosureProgramId": program_id,
        "fields":              fields or RESTAURANT_FIELDS,
        "filters":             filters,
    }

    resp = requests.post(
        VCH_FACILITIES_URL,
        json=payload,
        headers=VCH_HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def query_vch_inspections(program: str, facility_id: str) -> list:
    """Fetch inspection history for a single facility."""
    program_id = PROGRAM_IDS.get(program)
    url = f"{VCH_BASE_URL}/{program_id}/facility/{facility_id}/inspectionDetails/"
    resp = requests.get(url, headers=VCH_HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else data.get("result", [])


def query_vch_inspection_detail(program: str, inspection_id: str) -> dict:
    """Fetch full detail for a single inspection."""
    program_id = PROGRAM_IDS.get(program)
    url = f"{VCH_BASE_URL}/{program_id}/inspection/{inspection_id}/details/"
    resp = requests.get(url, headers=VCH_HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("result", data) if isinstance(data, dict) else data


# ─── Route registration ───────────────────────────────────────────────────────

def register_vch_routes(app: Flask):

    # ── Pages ─────────────────────────────────────────────────────────────────

    @app.route("/restaurants")
    def restaurants():
        return render_template("restaurant-map.html")

    @app.route("/childcare-map")
    def childcare_map():
        return render_template("childcare-map.html")

    # ── Facilities API ────────────────────────────────────────────────────────
    #
    # GET /api/vch/restaurants/facilities
    #     ?q=pizza          — search by name (optional)
    #     &community=Vancouver  — filter by community (optional)
    #     &page=0           — page number (default 0)
    #     &page_size=100    — results per page (default 100, max 500)
    #
    # GET /api/vch/childcare/facilities
    #     (same params)

    @app.route("/api/vch/<program>/facilities")
    def vch_facilities(program):
        if program not in PROGRAM_IDS:
            return jsonify({"error": f"Unknown program: {program}"}), 400

        criteria  = request.args.get("q", "")
        community = request.args.get("community", "")
        page      = int(request.args.get("page", 0))
        page_size = min(int(request.args.get("page_size", 100)), 500)

        # Cache key based on all params
        cache_key = _cache_key(program, criteria, community, page, page_size)
        cached = _cache_get(cache_key, ttl=CACHE_TTL_FACILITIES)
        if cached:
            return jsonify({**cached, "cached": True})

        try:
            fields = CHILDCARE_FIELDS if program == "childcare" else RESTAURANT_FIELDS
            data   = query_vch_facilities(
                program, criteria=criteria, community=community,
                page=page, page_size=page_size, fields=fields,
            )
            result = {
                "result":    data.get("result", []),
                "total":     data.get("total") or data.get("totalCount") or data.get("totalNumberOfRecords"),
                "page":      page,
                "page_size": page_size,
                "fetched_at": datetime.now().isoformat(),
            }
            _cache_set(cache_key, result)
            return jsonify(result)

        except requests.HTTPError as e:
            return jsonify({"error": f"VCH API error: {e}"}), 502
        except requests.Timeout:
            return jsonify({"error": "VCH API timed out"}), 504
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ── Inspections for one facility ──────────────────────────────────────────
    #
    # GET /api/vch/restaurants/facility/<facility_id>/inspections
    # GET /api/vch/childcare/facility/<facility_id>/inspections

    @app.route("/api/vch/<program>/facility/<facility_id>/inspections")
    def vch_facility_inspections(program, facility_id):
        if program not in PROGRAM_IDS:
            return jsonify({"error": f"Unknown program: {program}"}), 400

        cache_key = _cache_key("inspections", program, facility_id)
        cached = _cache_get(cache_key, ttl=CACHE_TTL_INSPECTIONS)
        if cached:
            return jsonify({"result": cached, "cached": True})

        try:
            inspections = query_vch_inspections(program, facility_id)
            # Only return latest 3 inspections
            inspections = sorted(
                inspections,
                key=lambda x: x.get("inspectionDate") or "",
                reverse=True
            )[:3]
            _cache_set(cache_key, inspections)
            return jsonify({"result": inspections})
        except requests.HTTPError as e:
            return jsonify({"error": str(e)}), 502
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ── Single inspection detail ───────────────────────────────────────────────
    #
    # GET /api/vch/restaurants/inspection/<inspection_id>/details
    # GET /api/vch/childcare/inspection/<inspection_id>/details

    @app.route("/api/vch/<program>/inspection/<inspection_id>/details")
    def vch_inspection_detail(program, inspection_id):
        if program not in PROGRAM_IDS:
            return jsonify({"error": f"Unknown program: {program}"}), 400

        cache_key = _cache_key("detail", program, inspection_id)
        cached = _cache_get(cache_key)
        if cached:
            return jsonify({"result": cached, "cached": True})

        try:
            detail = query_vch_inspection_detail(program, inspection_id)
            _cache_set(cache_key, detail)
            return jsonify({"result": detail})
        except requests.HTTPError as e:
            return jsonify({"error": str(e)}), 502
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ── Get all unique communities ───────────────────────────────────────────
    # GET /api/vch/restaurants/communities
    # Fetches all communities by getting a large page and extracting unique values

    @app.route("/api/vch/<program>/communities")
    def vch_communities(program):
        if program not in PROGRAM_IDS:
            return jsonify({"error": f"Unknown program: {program}"}), 400

        cache_key = _cache_key("communities", program)
        cached = _cache_get(cache_key, ttl=CACHE_TTL_FACILITIES)
        if cached:
            return jsonify({"communities": cached, "cached": True})

        try:
            program_id = PROGRAM_IDS[program]
            all_communities = set()
            page = 0
            PAGE_SIZE = 500

            # Paginate through ALL records to get every community
            while True:
                payload = {
                    "pageNumber":          page,
                    "pageSize":            PAGE_SIZE,
                    "criteria":            "",
                    "sort":                [{"field": "community", "order": "asc"}],
                    "disclosureProgramId": program_id,
                    "fields":              ["community"],
                    "filters":             [],
                }
                resp = requests.post(VCH_FACILITIES_URL, json=payload, headers=VCH_HEADERS, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                results = data.get("result", [])

                for r in results:
                    if r.get("community"):
                        all_communities.add(r["community"])

                total = data.get("total") or data.get("totalCount") or data.get("totalNumberOfRecords") or 0
                fetched_so_far = (page + 1) * PAGE_SIZE

                if not results or fetched_so_far >= int(total) or len(results) < PAGE_SIZE:
                    break
                page += 1

            communities = sorted(all_communities)
            _cache_set(cache_key, communities)
            return jsonify({"communities": communities})

        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ── Cache status (useful for debugging) ───────────────────────────────────

    @app.route("/api/vch/cache/status")
    def vch_cache_status():
        now = _time.time()
        entries = [
            {
                "key":        k,
                "age_seconds": int(now - v["ts"]),
                "expires_in":  max(0, int(CACHE_TTL - (now - v["ts"]))),
            }
            for k, v in _cache.items()
        ]
        return jsonify({
            "total_entries": len(_cache),
            "ttl_seconds":   CACHE_TTL,
            "entries":       entries,
        })

    @app.route("/api/vch/cache/clear", methods=["POST"])
    def vch_cache_clear():
        _cache.clear()
        return jsonify({"message": "Cache cleared"})
