# translink_routes.py
# Add to your existing app.py:
#
#   from translink_routes import register_translink_routes
#   register_translink_routes(app)
#
# Requirements:
#   pip install requests gtfs-realtime-bindings protobuf python-dotenv
#
# .env file:
#   TRANSLINK_API_KEY=your_key_here

import os
import time
import requests
from flask import Flask, jsonify
from google.transit import gtfs_realtime_pb2

# ─── Config ───────────────────────────────────────────────────────────────────

TRANSLINK_API_KEY = os.environ.get("TRANSLINK_API_KEY", "CtSKGdCGGKoUhieI3v8l")
POSITIONS_URL     = f"https://gtfsapi.translink.ca/v3/gtfsposition?apikey={TRANSLINK_API_KEY}"
ALERTS_URL        = f"https://gtfsapi.translink.ca/v3/gtfsalerts?apikey={TRANSLINK_API_KEY}"

# Cache position feed for 8 seconds (feed updates every 10s)
_positions_cache = {"data": None, "ts": 0}
CACHE_TTL = 8

VEHICLE_TYPES = {
    0: "Tram",
    1: "SkyTrain",
    2: "Rail",
    3: "Bus",
    4: "Ferry",
    5: "Cable Car",
    6: "Gondola",
    7: "Funicular",
}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def fetch_positions() -> list[dict]:
    """
    Fetch and decode the GTFS Realtime position protobuf feed.
    Returns a list of vehicle position dicts.
    """
    now = time.time()
    if _positions_cache["data"] and (now - _positions_cache["ts"]) < CACHE_TTL:
        return _positions_cache["data"]

    resp = requests.get(POSITIONS_URL, timeout=10)
    resp.raise_for_status()

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(resp.content)

    vehicles = []
    for entity in feed.entity:
        if not entity.HasField("vehicle"):
            continue
        v   = entity.vehicle
        pos = v.position

        vehicles.append({
            "id":        entity.id,
            "lat":       pos.latitude,
            "lon":       pos.longitude,
            "bearing":   pos.bearing  if pos.HasField("bearing")  else None,
            "speed":     round(pos.speed * 3.6, 1) if pos.HasField("speed") else None,  # m/s → km/h
            "routeId":   v.trip.route_id   if v.HasField("trip") else None,
            "tripId":    v.trip.trip_id    if v.HasField("trip") else None,
            "vehicleId": v.vehicle.id      if v.HasField("vehicle") else entity.id,
            "label":     v.vehicle.label   if v.HasField("vehicle") else None,
            "timestamp": v.timestamp       if v.HasField("timestamp") else None,
        })

    _positions_cache["data"] = vehicles
    _positions_cache["ts"]   = now
    return vehicles


# ─── Route registration ───────────────────────────────────────────────────────

def register_translink_routes(app: Flask):

    @app.route("/api/transit/positions")
    def transit_positions():
        """
        GET /api/transit/positions
        Returns all live vehicle positions as JSON.
        Cached for 8 seconds to avoid hammering TransLink.
        """
        try:
            vehicles = fetch_positions()
            return jsonify({
                "vehicles": vehicles,
                "count":    len(vehicles),
                "cached_at": _positions_cache["ts"],
            })
        except requests.HTTPError as e:
            return jsonify({"error": f"TransLink API error: {e}"}), 502
        except Exception as e:
            return jsonify({"error": str(e)}), 500