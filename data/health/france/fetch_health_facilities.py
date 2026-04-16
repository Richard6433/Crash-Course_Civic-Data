"""
fetch_health_facilities.py
==========================
Queries the Overpass API (OpenStreetMap) for health facilities in
metropolitan France and saves them as classified GeoJSON files.

Output files
------------
data/health/france/
  hospitals.geojson     — Hospitals (CHU, CHR, private clinics, etc.)
  clinics.geojson       — Clinics and outpatient centres
  pharmacies.geojson    — Pharmacies
  doctors.geojson       — GP / specialist surgeries
  dentists.geojson      — Dental practices
  all_classified.geojson — All of the above in one file, with a
                           "facility_type" property for easy filtering

Run with:  python3 fetch_health_facilities.py
"""

import json
import time
import urllib.request
import urllib.parse
import urllib.error

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Metropolitan France bounding box (excludes overseas territories)
# south, west, north, east
FRANCE_BBOX = "41.3,-5.1,51.1,9.6"

# Each entry: (filename_stem, amenity_tag, display_label, marker_colour)
FACILITY_TYPES = [
    ("hospitals",  "hospital",  "Hospital",  "#e63946"),
    ("clinics",    "clinic",    "Clinic",    "#f4a261"),
    ("pharmacies", "pharmacy",  "Pharmacy",  "#2a9d8f"),
    ("doctors",    "doctors",   "Doctor",    "#457b9d"),
    ("dentists",   "dentist",   "Dentist",   "#6a4c93"),
]

OUTPUT_DIR = "."   # script lives in the output dir already


def build_query(amenity: str, bbox: str) -> str:
    """Return an Overpass QL query for a single amenity type within a bbox."""
    return f"""
[out:json][timeout:90];
(
  node["amenity"="{amenity}"]({bbox});
  way["amenity"="{amenity}"]({bbox});
  relation["amenity"="{amenity}"]({bbox});
);
out center tags;
"""


def fetch(query: str) -> dict:
    """POST the query to the Overpass API and return parsed JSON."""
    data = urllib.parse.urlencode({"data": query}).encode()
    req  = urllib.request.Request(OVERPASS_URL, data=data,
                                  headers={"User-Agent": "civic-data-course/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


def element_to_feature(el: dict, label: str, colour: str) -> dict | None:
    """
    Convert an Overpass element to a GeoJSON Feature.
    Ways/relations come back with a synthetic 'center' key.
    Returns None if coordinates cannot be determined.
    """
    tags = el.get("tags", {})

    if el["type"] == "node":
        lon, lat = el.get("lon"), el.get("lat")
    else:                               # way or relation
        center = el.get("center", {})
        lon, lat = center.get("lon"), center.get("lat")

    if lon is None or lat is None:
        return None

    return {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [lon, lat]
        },
        "properties": {
            "osm_id":        el.get("id"),
            "osm_type":      el["type"],
            "facility_type": label,
            "colour":        colour,
            "name":          tags.get("name", ""),
            "operator":      tags.get("operator", ""),
            "address":       " ".join(filter(None, [
                                 tags.get("addr:housenumber", ""),
                                 tags.get("addr:street", ""),
                                 tags.get("addr:postcode", ""),
                                 tags.get("addr:city", ""),
                             ])),
            "phone":         tags.get("phone", tags.get("contact:phone", "")),
            "website":       tags.get("website", tags.get("contact:website", "")),
            "emergency":     tags.get("emergency", ""),
            "beds":          tags.get("beds", ""),
            "opening_hours": tags.get("opening_hours", ""),
            "wheelchair":    tags.get("wheelchair", ""),
        }
    }


def to_geojson(features: list) -> dict:
    return {"type": "FeatureCollection", "features": features}


def main():
    all_features = []

    for stem, amenity, label, colour in FACILITY_TYPES:
        print(f"  Fetching {label}s …", end=" ", flush=True)
        try:
            raw = fetch(build_query(amenity, FRANCE_BBOX))
        except urllib.error.URLError as e:
            print(f"FAILED ({e})")
            continue

        features = []
        for el in raw.get("elements", []):
            feat = element_to_feature(el, label, colour)
            if feat:
                features.append(feat)

        print(f"{len(features)} found")

        # Save individual file
        path = f"{OUTPUT_DIR}/{stem}.geojson"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(to_geojson(features), f, ensure_ascii=False, indent=2)
        print(f"    Saved → {path}")

        all_features.extend(features)

        # Polite pause between requests
        time.sleep(3)

    # Save combined file
    combined_path = f"{OUTPUT_DIR}/all_classified.geojson"
    with open(combined_path, "w", encoding="utf-8") as f:
        json.dump(to_geojson(all_features), f, ensure_ascii=False, indent=2)
    print(f"\nCombined file ({len(all_features)} total features) → {combined_path}")

    # Print a quick summary
    print("\nSummary")
    print("-------")
    by_type = {}
    for feat in all_features:
        t = feat["properties"]["facility_type"]
        by_type[t] = by_type.get(t, 0) + 1
    for t, n in by_type.items():
        print(f"  {t:<12} {n:>6,}")
    print(f"  {'TOTAL':<12} {len(all_features):>6,}")


if __name__ == "__main__":
    print("Fetching French health facilities from OpenStreetMap / Overpass API")
    print("This may take a few minutes …\n")
    main()
    print("\nDone.")
