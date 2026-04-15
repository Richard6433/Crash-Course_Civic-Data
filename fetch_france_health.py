"""
Fetches hospital and clinic locations in France from the Overpass API
(OpenStreetMap data) and saves the results as:
  - france_health.csv   (easy to inspect in a spreadsheet)
  - france_health.geojson (ready to load directly in Leaflet)
"""

import json
import csv
import urllib.request
import urllib.parse

# ── Overpass query ────────────────────────────────────────────────────────────
# We ask for every node/way/relation tagged as a hospital or clinic
# inside the administrative boundary of France (admin_level=2, ISO code FR).
# "out center tags" means: for ways/relations give us one centre-point
# coordinate, and include all the name/type tags.
QUERY = """
[out:json][timeout:120];
area["ISO3166-1"="FR"]["admin_level"="2"]->.france;
(
  node["amenity"~"^(hospital|clinic)$"](area.france);
  way["amenity"~"^(hospital|clinic)$"](area.france);
  relation["amenity"~"^(hospital|clinic)$"](area.france);
);
out center tags;
"""

print("Querying Overpass API for hospitals and clinics in France…")
print("(This may take up to 60 seconds)")

url  = "https://overpass-api.de/api/interpreter"
data = urllib.parse.urlencode({"data": QUERY}).encode()
req  = urllib.request.Request(url, data=data,
                               headers={"User-Agent": "civic-data-course/1.0"})

with urllib.request.urlopen(req, timeout=130) as resp:
    raw = json.loads(resp.read().decode())

elements = raw.get("elements", [])
print(f"  → {len(elements)} features returned by the API")

# ── Parse into a flat list of records ────────────────────────────────────────
records = []
for el in elements:
    # Nodes have lat/lon directly; ways and relations expose a "center" object
    lat = el.get("lat") or (el.get("center") or {}).get("lat")
    lon = el.get("lon") or (el.get("center") or {}).get("lon")
    if not (lat and lon):
        continue

    tags = el.get("tags", {})
    records.append({
        "osm_id":    el["id"],
        "osm_type":  el["type"],
        "lat":       lat,
        "lon":       lon,
        "name":      tags.get("name", ""),
        "amenity":   tags.get("amenity", ""),       # hospital | clinic
        "operator":  tags.get("operator", ""),
        "emergency": tags.get("emergency", ""),     # yes / no
        "beds":      tags.get("beds", ""),
        "phone":     tags.get("phone", ""),
        "website":   tags.get("website", ""),
        "addr_city": tags.get("addr:city", ""),
    })

print(f"  → {len(records)} features with valid coordinates")

# ── Save as CSV ───────────────────────────────────────────────────────────────
csv_path = "france_health.csv"
with open(csv_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=records[0].keys())
    writer.writeheader()
    writer.writerows(records)
print(f"Saved CSV  → {csv_path}")

# ── Save as GeoJSON ───────────────────────────────────────────────────────────
features = []
for r in records:
    props = {k: v for k, v in r.items() if k not in ("lat", "lon")}
    features.append({
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [r["lon"], r["lat"]]   # GeoJSON uses [lon, lat]
        },
        "properties": props
    })

geojson = {
    "type": "FeatureCollection",
    "features": features
}

geojson_path = "france_health.geojson"
with open(geojson_path, "w", encoding="utf-8") as f:
    json.dump(geojson, f, ensure_ascii=False)
print(f"Saved GeoJSON → {geojson_path}")
print("Done.")
