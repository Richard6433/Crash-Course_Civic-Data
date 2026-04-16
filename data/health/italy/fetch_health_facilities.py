"""
fetch_health_facilities.py  (Italy)
====================================
Queries the Overpass API (OpenStreetMap) for health facilities in
Italy and saves them as classified GeoJSON files.

Output files
------------
data/health/italy/
  hospitals.geojson     — Hospitals (public and private)
  clinics.geojson       — Clinics and outpatient centres
  pharmacies.geojson    — Pharmacies (farmacie)
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

# Italy bounding box (includes Sicily and Sardinia)
# south, west, north, east
ITALY_BBOX = "35.4,6.6,47.1,18.8"

# Each entry: (filename_stem, amenity_tag, display_label, marker_colour)
FACILITY_TYPES = [
    ("clinics",    "clinic",    "Clinic",    "#f4a261"),
    ("pharmacies", "pharmacy",  "Pharmacy",  "#2a9d8f"),
    ("doctors",    "doctors",   "Doctor",    "#457b9d"),
    ("dentists",   "dentist",   "Dentist",   "#6a4c93"),
]

# Hospitals are fetched separately in two halves to avoid timeouts
HOSPITAL_BBOXES = [
    ("35.4,6.6,42.0,18.8", "south"),   # Southern Italy + islands
    ("42.0,6.6,47.1,18.8", "north"),   # Northern + Central Italy
]

OUTPUT_DIR = "."


def build_query(amenity: str, bbox: str) -> str:
    return (
        f'[out:json][timeout:90];'
        f'(node["amenity"="{amenity}"]({bbox});'
        f'way["amenity"="{amenity}"]({bbox});'
        f'relation["amenity"="{amenity}"]({bbox}););'
        f'out center tags;'
    )


def fetch(query: str) -> dict:
    data = urllib.parse.urlencode({"data": query}).encode()
    req  = urllib.request.Request(
        OVERPASS_URL, data=data,
        headers={"User-Agent": "civic-data-course/1.0"}
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


def fetch_with_retry(query: str) -> dict | None:
    for attempt in range(1, 5):
        try:
            return fetch(query)
        except urllib.error.URLError as e:
            wait = 2 ** attempt
            print(f"(attempt {attempt} failed: {e}, retrying in {wait}s...)", end=" ", flush=True)
            time.sleep(wait)
    return None


def element_to_feature(el: dict, label: str, colour: str) -> dict | None:
    tags = el.get("tags", {})
    if el["type"] == "node":
        lon, lat = el.get("lon"), el.get("lat")
    else:
        center = el.get("center", {})
        lon, lat = center.get("lon"), center.get("lat")
    if lon is None or lat is None:
        return None
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "osm_id":        el.get("id"),
            "osm_type":      el["type"],
            "facility_type": label,
            "colour":        colour,
            "name":          tags.get("name", ""),
            "operator":      tags.get("operator", ""),
            "address": " ".join(filter(None, [
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

    # --- Hospitals (split into two halves) ---
    print("  Fetching Hospitals ...")
    hospital_features = []
    seen_ids = set()
    for bbox, label in HOSPITAL_BBOXES:
        print(f"    [{label}] ...", end=" ", flush=True)
        raw = fetch_with_retry(build_query("hospital", bbox))
        if raw is None:
            print("SKIPPED")
            continue
        n = 0
        for el in raw.get("elements", []):
            eid = (el["type"], el.get("id"))
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            feat = element_to_feature(el, "Hospital", "#e63946")
            if feat:
                hospital_features.append(feat)
                n += 1
        print(f"{n} found")
        time.sleep(3)

    path = f"{OUTPUT_DIR}/hospitals.geojson"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(to_geojson(hospital_features), f, ensure_ascii=False, indent=2)
    print(f"    Saved → {path}  ({len(hospital_features)} total)")
    all_features.extend(hospital_features)

    # --- Other facility types ---
    for stem, amenity, label, colour in FACILITY_TYPES:
        print(f"  Fetching {label}s ...", end=" ", flush=True)
        raw = fetch_with_retry(build_query(amenity, ITALY_BBOX))
        if raw is None:
            print("SKIPPED")
            continue
        features = []
        for el in raw.get("elements", []):
            feat = element_to_feature(el, label, colour)
            if feat:
                features.append(feat)
        print(f"{len(features)} found")
        path = f"{OUTPUT_DIR}/{stem}.geojson"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(to_geojson(features), f, ensure_ascii=False, indent=2)
        print(f"    Saved → {path}")
        all_features.extend(features)
        time.sleep(3)

    # --- Combined file ---
    combined_path = f"{OUTPUT_DIR}/all_classified.geojson"
    with open(combined_path, "w", encoding="utf-8") as f:
        json.dump(to_geojson(all_features), f, ensure_ascii=False, indent=2)

    print(f"\nCombined file → {combined_path}")
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
    print("Fetching Italian health facilities from OpenStreetMap / Overpass API")
    print("This may take a few minutes ...\n")
    main()
    print("\nDone.")
