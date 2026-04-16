"""
compute_at_risk.py
==================
Finds all Italian health facilities that lie within THRESHOLD_KM of
any flood event recorded in italy_floods.csv and saves the result as
at_risk_facilities.geojson.

It also writes at_risk_ids.json — a set of osm_id values — so the
Leaflet map can highlight at-risk sites without loading two files.

Algorithm
---------
For every health facility we check its distance to every flood event
using the Haversine formula (great-circle distance). If any flood event
is within the threshold, the facility is flagged.

Haversine is accurate enough for distances < 100 km and requires no
external libraries — only Python's standard math module.

Run with:  python3 compute_at_risk.py
(from the repo root or from data/health/italy/)
"""

import csv
import json
import math
import os

# ── Configuration ──────────────────────────────────────────────────────────────
THRESHOLD_KM = 1.0          # flag facilities within this distance of a flood
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT    = os.path.join(SCRIPT_DIR, "..", "..", "..")

FLOODS_CSV   = os.path.join(REPO_ROOT, "italy_floods.csv")
HEALTH_GEOJSON = os.path.join(SCRIPT_DIR, "all_classified.geojson")
OUTPUT_AT_RISK = os.path.join(SCRIPT_DIR, "at_risk_facilities.geojson")


# ── Haversine distance ──────────────────────────────────────────────────────────
def haversine_km(lat1, lon1, lat2, lon2):
    """Return the great-circle distance in km between two (lat, lon) points."""
    R = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(d_lon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


# ── Load flood events ───────────────────────────────────────────────────────────
print("Loading flood events …")
flood_points = []   # list of (lat, lon, metadata)
with open(FLOODS_CSV, newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        try:
            flood_points.append({
                "lat":   float(row["lat"]),
                "lon":   float(row["long"]),
                "began": row["Began"][:10],
                "cause": row["MainCause"],
                "sev":   row["Severity"],
            })
        except (ValueError, KeyError):
            continue
print(f"  {len(flood_points)} flood events loaded")


# ── Load health facilities ──────────────────────────────────────────────────────
print("Loading health facilities …")
with open(HEALTH_GEOJSON, encoding="utf-8") as f:
    health_fc = json.load(f)
features = health_fc["features"]
print(f"  {len(features):,} facilities loaded")


# ── Proximity check ─────────────────────────────────────────────────────────────
print(f"Checking proximity (threshold = {THRESHOLD_KM} km) …")
at_risk_features = []

for feat in features:
    coords = feat["geometry"]["coordinates"]
    fac_lon, fac_lat = coords[0], coords[1]

    closest_km   = None
    closest_flood = None

    for flood in flood_points:
        d = haversine_km(fac_lat, fac_lon, flood["lat"], flood["lon"])
        if closest_km is None or d < closest_km:
            closest_km    = d
            closest_flood = flood

    if closest_km is not None and closest_km <= THRESHOLD_KM:
        # Enrich the feature with proximity metadata
        feat["properties"]["at_risk"]          = True
        feat["properties"]["nearest_flood_km"] = round(closest_km, 3)
        feat["properties"]["flood_began"]      = closest_flood["began"]
        feat["properties"]["flood_cause"]      = closest_flood["cause"]
        feat["properties"]["flood_severity"]   = closest_flood["sev"]
        at_risk_features.append(feat)


# ── Save outputs ────────────────────────────────────────────────────────────────
at_risk_fc = {"type": "FeatureCollection", "features": at_risk_features}
with open(OUTPUT_AT_RISK, "w", encoding="utf-8") as f:
    json.dump(at_risk_fc, f, ensure_ascii=False, indent=2)

print(f"\nResults")
print(f"-------")
print(f"  At-risk facilities : {len(at_risk_features):,}")
print(f"  Total facilities   : {len(features):,}")
print(f"  Saved → {OUTPUT_AT_RISK}")

# ── Breakdown by type ───────────────────────────────────────────────────────────
by_type = {}
for feat in at_risk_features:
    t = feat["properties"]["facility_type"]
    by_type[t] = by_type.get(t, 0) + 1
print("\nBreakdown by type:")
for t, n in sorted(by_type.items()):
    print(f"  {t:<12} {n:>5,}")
