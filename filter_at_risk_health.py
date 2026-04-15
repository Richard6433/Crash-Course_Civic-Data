"""
Finds health facilities within 1 km of any France flood event centroid
and saves them to france_health_at_risk.csv.

How it works:
  For every health site we calculate its distance to each of the 41 flood
  event centroids using the Haversine formula (the correct way to measure
  distances on a curved Earth). If the shortest distance found is <= 1 km,
  the site is flagged as "at risk" and included in the output.

Two extra columns are added to explain each match:
  nearest_flood_id  - the ID of the closest flood event
  nearest_flood_km  - the actual distance in km (rounded to 3 decimal places)

⚠️  Important caveat:
  The flood coordinates are the CENTROID of each affected area, not a
  precise flood boundary. Some events cover hundreds of thousands of km².
  "Within 1 km of the centroid" is therefore a conservative proxy — it
  flags sites very close to where the flood was centred.
"""

import csv
from math import radians, sin, cos, sqrt, atan2


# ── Haversine formula ─────────────────────────────────────────────────────────
def haversine_km(lat1, lon1, lat2, lon2):
    """Return the great-circle distance in km between two lat/lon points."""
    R = 6371.0  # mean Earth radius in km
    d_lat = radians(lat2 - lat1)
    d_lon = radians(lon2 - lon1)
    a = (sin(d_lat / 2) ** 2
         + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lon / 2) ** 2)
    return 2 * R * atan2(sqrt(a), sqrt(1 - a))


THRESHOLD_KM = 1.0


# ── Load flood centroids ──────────────────────────────────────────────────────
floods = []
with open("france_floods.csv", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        floods.append({
            "id":  row["ID"],
            "lat": float(row["lat"]),
            "lon": float(row["long"]),   # note: column is called "long" here
        })

print(f"Loaded {len(floods)} flood events")


# ── Load health facilities ────────────────────────────────────────────────────
health = []
with open("france_health.csv", encoding="utf-8") as f:
    health = list(csv.DictReader(f))

print(f"Loaded {len(health)} health facilities")


# ── Find facilities within threshold ─────────────────────────────────────────
at_risk = []

for site in health:
    site_lat = float(site["lat"])
    site_lon = float(site["lon"])

    # Find the closest flood event and its distance
    closest_id  = None
    closest_km  = float("inf")

    for flood in floods:
        km = haversine_km(site_lat, site_lon, flood["lat"], flood["lon"])
        if km < closest_km:
            closest_km = km
            closest_id = flood["id"]

    # Keep this site if it falls within 1 km of any flood centroid
    if closest_km <= THRESHOLD_KM:
        row = dict(site)                           # copy all original columns
        row["nearest_flood_id"] = closest_id
        row["nearest_flood_km"] = round(closest_km, 3)
        at_risk.append(row)


print(f"Health sites within {THRESHOLD_KM} km of a flood centroid: {len(at_risk)}")


# ── Save result ───────────────────────────────────────────────────────────────
if at_risk:
    out_path = "france_health_at_risk.csv"
    fieldnames = list(at_risk[0].keys())

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(at_risk)

    print(f"Saved → {out_path}")
else:
    print("No sites matched — no file written.")
    print("Consider increasing THRESHOLD_KM at the top of the script.")
