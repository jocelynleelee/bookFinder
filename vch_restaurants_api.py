"""
VCH Food Premises (Restaurants) Fetcher — Incremental Pipeline
=======================================================
Only fetches data that isn't already present locally:
  - Skips Step 1 if vch_restaurants.json already exists
  - Skips Step 2 for facilities already in vch_restaurant_inspections.json
  - Skips Step 3 for inspections already in vch_restaurant_inspection_details.json

Run with --force to re-fetch everything from scratch.

Usage:
    pip install requests
    python vch_restaurants_api.py           # incremental (only fetch missing)
    python vch_restaurants_api.py --force   # full re-fetch

Output:
    vch_restaurants.json
    vch_restaurant_inspections.json
    vch_restaurant_inspection_details.json
    vch_restaurants.csv
    vch_restaurant_inspections.csv
    vch_restaurant_inspection_details.csv
"""

import requests
import json
import csv
import time
import sys
from datetime import datetime
from pathlib import Path

# ─── Configuration ────────────────────────────────────────────────────────────

PROGRAM_ID            = "bfecaebb-8c76-43aa-be0d-6a00092ac5f1"
BASE_URL              = "https://inspections.vch.ca/api/v0/portal/disclosure/program"
FACILITIES_URL        = "https://inspections.vch.ca/api/v0/portal/disclosure/program/facilities"
INSPECTIONS_URL       = f"{BASE_URL}/{PROGRAM_ID}/facility/{{facility_id}}/inspectionDetails/"
INSPECTION_DETAIL_URL = f"{BASE_URL}/{PROGRAM_ID}/inspection/{{inspection_id}}/details/"

PAGE_SIZE    = 100
POLITE_DELAY = 0.2  # seconds between requests

FIELDS = [
    "community", "facilityName", "facilityType", "phoneNumber",
    "siteAddress", "latitude", "longitude", "emailAddress", "website",
    "lastInspectionDate", "hazardRating", "hazardScore", "totalInfractions",
    "outstandingCriticalInfractions", "outstandingNonCriticalInfractions",
    "closure", "operationsType",
]

HEADERS = {
    "Content-Type": "application/json",
    "Accept":       "application/json",
    "Origin":       "https://inspections.vch.ca",
    "Referer":      "https://inspections.vch.ca/",
    "User-Agent":   "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

FORCE = "--force" in sys.argv

# ─── Load / save helpers ──────────────────────────────────────────────────────

def load_json(path: str) -> list:
    """Load existing JSON file, return empty list if not found."""
    p = Path(path)
    if p.exists():
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    return []


def save_json(data, path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  Saved JSON → {path}")


def save_csv(records: list[dict], path: str):
    if not records:
        print(f"  No records to save for {path}")
        return
    flat_records = [flatten(r) for r in records]
    all_keys = []
    for r in flat_records:
        for k in r.keys():
            if k not in all_keys:
                all_keys.append(k)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(flat_records)
    print(f"  Saved CSV  → {path}")


def flatten(record, prefix="") -> dict:
    flat = {}
    for k, v in record.items():
        key = f"{prefix}{k}" if not prefix else f"{prefix}_{k}"
        if isinstance(v, dict):
            flat.update(flatten(v, prefix=key))
        elif isinstance(v, list):
            flat[key] = json.dumps(v)
        else:
            flat[key] = v
    return flat


# ─── Step 1: Fetch all facilities ─────────────────────────────────────────────

def fetch_facilities_page(criteria: str = "", page_number: int = 0) -> dict:
    payload = {
        "pageNumber": page_number,
        "pageSize": PAGE_SIZE,
        "criteria": criteria,
        "sort": [{"field": "community", "order": "asc"}],
        "disclosureProgramId": PROGRAM_ID,
        "fields": FIELDS,
        "filters": []
    }
    resp = requests.post(FACILITIES_URL, json=payload, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_all_facilities() -> list[dict]:
    all_facilities = []
    page = 0
    total = None
    while True:
        print(f"  Page {page + 1}..." + (f" of ~{-(-total // PAGE_SIZE)}" if total else ""))
        data = fetch_facilities_page(page_number=page)
        facilities = data.get("result", [])
        if not facilities:
            break
        all_facilities.extend(facilities)
        if total is None:
            total = data.get("total") or data.get("totalCount") or data.get("totalNumberOfRecords")
            if total:
                print(f"  Total available: {total}")
        print(f"  +{len(facilities)} (running total: {len(all_facilities)})")
        if (total and len(all_facilities) >= int(total)) or len(facilities) < PAGE_SIZE:
            break
        page += 1
        time.sleep(POLITE_DELAY)
    return all_facilities


# ─── Step 2: Fetch inspection list per facility ────────────────────────────────

def fetch_facility_inspections(facility_id: str) -> list[dict]:
    url = INSPECTIONS_URL.format(facility_id=facility_id)
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        return data
    return data.get("result") or data.get("inspections") or data.get("data") or []


# ─── Step 3: Fetch full details for one inspection ────────────────────────────

def fetch_inspection_details(inspection_id: str) -> dict:
    url = INSPECTION_DETAIL_URL.format(inspection_id=inspection_id)
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        return data.get("result") or data
    return data


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    ts = datetime.now().isoformat()
    print("=" * 60)
    print("VCH Food Premises Incremental Pipeline")
    print(f"Program ID : {PROGRAM_ID}")
    print(f"Started    : {ts}")
    print(f"Mode       : {'FORCE (full re-fetch)' if FORCE else 'Incremental (skip existing)'}")
    print("=" * 60)

    # ── Step 1: Facilities ─────────────────────────────────────────────────────
    existing_facilities = [] if FORCE else load_json("vch_restaurants.json")

    if existing_facilities and not FORCE:
        print(f"\n[Step 1] Skipping — vch_restaurants.json already has {len(existing_facilities)} restaurants.")
        print("         Run with --force to re-fetch.")
        facilities = existing_facilities
    else:
        print("\n[Step 1] Fetching all facilities...")
        facilities = fetch_all_facilities()
        if not facilities:
            print("No facilities returned. Exiting.")
            return
        for f in facilities:
            f["scraped_at"] = ts
        print(f"\n  Total facilities: {len(facilities)}")
        save_json(facilities, "vch_restaurants.json")
        save_csv(facilities, "vch_restaurants.csv")

    # ── Step 2: Inspections per facility ──────────────────────────────────────
    existing_inspections = [] if FORCE else load_json("vch_restaurant_inspections.json")

    # Build set of facility IDs already fetched
    already_fetched_fids = set(i["_facilityId"] for i in existing_inspections if "_facilityId" in i)
    missing_facilities   = [f for f in facilities if f["id"] not in already_fetched_fids]

    if not missing_facilities and not FORCE:
        print(f"\n[Step 2] Skipping — all {len(facilities)} facilities already have inspection data.")
        all_inspections = existing_inspections
    else:
        if FORCE:
            print(f"\n[Step 2] Fetching inspections for all {len(facilities)} facilities...")
            targets = facilities
            all_inspections = []
        else:
            print(f"\n[Step 2] Fetching inspections for {len(missing_facilities)} new facilities")
            print(f"         (skipping {len(already_fetched_fids)} already fetched)...")
            targets = missing_facilities
            all_inspections = existing_inspections.copy()

        for i, facility in enumerate(targets):
            fid  = facility["id"]
            name = facility.get("facilityName", fid)
            print(f"  [{i+1}/{len(targets)}] {name}")
            try:
                inspections = fetch_facility_inspections(fid)
                for insp in inspections:
                    insp["_facilityId"]   = fid
                    insp["_facilityName"] = name
                    insp["scraped_at"]    = ts
                all_inspections.extend(inspections)
                print(f"    → {len(inspections)} inspections")
            except requests.HTTPError as e:
                print(f"    → HTTP error: {e}")
            except Exception as e:
                print(f"    → Error: {e}")
            time.sleep(POLITE_DELAY)

        print(f"\n  Total inspection records: {len(all_inspections)}")
        save_json(all_inspections, "vch_restaurant_inspections.json")
        save_csv(all_inspections, "vch_restaurant_inspections.csv")

    # ── Step 3: Inspection details ─────────────────────────────────────────────
    existing_details = [] if FORCE else load_json("vch_restaurant_inspection_details.json")

    # Build set of inspection IDs already fetched
    already_fetched_iids = set(d["id"] for d in existing_details if "id" in d)

    # Collect all inspection IDs we need
    all_inspection_ids = [
        (insp.get("id"), insp.get("_facilityId"), insp.get("_facilityName"))
        for insp in all_inspections
        if insp.get("id")
    ]
    missing_inspection_ids = [
        (iid, fid, fname)
        for iid, fid, fname in all_inspection_ids
        if iid not in already_fetched_iids
    ]

    if not missing_inspection_ids and not FORCE:
        print(f"\n[Step 3] Skipping — all {len(existing_details)} inspection details already fetched.")
        all_details = existing_details
    else:
        if FORCE:
            print(f"\n[Step 3] Fetching details for all {len(all_inspection_ids)} inspections...")
            targets_detail = all_inspection_ids
            all_details = []
        else:
            print(f"\n[Step 3] Fetching details for {len(missing_inspection_ids)} new inspections")
            print(f"         (skipping {len(already_fetched_iids)} already fetched)...")
            targets_detail = missing_inspection_ids
            all_details = existing_details.copy()

        for i, (iid, fid, fname) in enumerate(targets_detail):
            print(f"  [{i+1}/{len(targets_detail)}] {iid} ({fname})")
            try:
                detail = fetch_inspection_details(iid)
                if isinstance(detail, dict):
                    detail["_inspectionId"] = iid
                    detail["_facilityId"]   = fid
                    detail["_facilityName"] = fname
                    detail["scraped_at"]    = ts
                all_details.append(detail)
            except requests.HTTPError as e:
                print(f"    → HTTP error: {e}")
            except Exception as e:
                print(f"    → Error: {e}")
            time.sleep(POLITE_DELAY)

        print(f"\n  Total inspection details: {len(all_details)}")
        save_json(all_details, "vch_restaurant_inspection_details.json")
        save_csv(all_details, "vch_restaurant_inspection_details.csv")

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"  Facilities         : {len(facilities)}")
    print(f"  Inspection records : {len(all_inspections)}")
    print(f"  Inspection details : {len(all_details)}")
    print(f"\n  Output files:")
    print(f"    vch_facilities.json / .csv")
    print(f"    vch_inspections.json / .csv")
    print(f"    vch_inspection_details.json / .csv")
    print("\nDone!")


if __name__ == "__main__":
    main()
