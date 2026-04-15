"""
Step 1 — Data acquisition
Fetches all raw data needed for the wildfire vulnerability score.

Sources:
  municipalities : GADM Italy level-3 JSON  (no auth)
  fire exposure  : EFFIS severity TIFFs 2021+2023 via rasterio VSI (no auth)
  facilities     : Nominatim OSM (fire stations + civil protection)  (no auth)
  population     : OSM extratags via Nominatim  (no auth)
  elderly %      : Italian national average 23.5 %  (documented approximation)
"""

import json, os, time, zipfile, io
from pathlib import Path
import requests, urllib3
urllib3.disable_warnings()

RAW = Path(__file__).parent / "raw"
RAW.mkdir(exist_ok=True)

SESSION = requests.Session()
SESSION.verify = False
SESSION.headers.update({"User-Agent": "WildfireRiskDashboard/1.0 research-project"})

# ── region bounding boxes (W, S, E, N) ────────────────────────────────────
REGIONS = {
    "sicily":   (12.0, 36.0, 16.0, 38.0),
    "sardinia": ( 8.0, 38.0, 10.0, 42.0),
}

# ── 1. GADM municipality boundaries ───────────────────────────────────────
GADM_URL = "https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_ITA_3.json.zip"
# Sicily = GID_1 "ITA.15_1"   Sardinia = GID_1 "ITA.16_1"
REGION_GIDS = {"sicily": "ITA.15_1", "sardinia": "ITA.16_1"}

def fetch_gadm():
    out = RAW / "gadm_ita_3.json"
    if out.exists():
        print(f"  [GADM] already cached ({out.stat().st_size//1024} KB)")
        return
    print("  [GADM] downloading …", end=" ", flush=True)
    r = SESSION.get(GADM_URL, timeout=120)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        names = z.namelist()
        json_name = next(n for n in names if n.endswith(".json"))
        data = z.read(json_name)
    out.write_bytes(data)
    print(f"done ({len(data)//1024} KB)")

def filter_gadm():
    """Keep only Sicily + Sardinia municipalities and save as two GeoJSONs."""
    src = json.loads((RAW / "gadm_ita_3.json").read_text())
    for island, gid in REGION_GIDS.items():
        feats = [f for f in src["features"]
                 if f["properties"].get("GID_1") == gid]
        fc = {"type": "FeatureCollection", "features": feats}
        out = RAW / f"gadm_{island}.geojson"
        out.write_text(json.dumps(fc))
        print(f"  [GADM] {island}: {len(feats)} municipalities → {out.name}")

# ── 2. EFFIS severity TIFFs (read metadata only here; processing in step 2) ──
EFFIS_TIFF_URLS = {
    2021: "https://data.effis.emergency.copernicus.eu/effis/applications/data-and-services/severity_2021.tiff",
    2023: "https://data.effis.emergency.copernicus.eu/effis/applications/data-and-services/severity_2023.tiff",
}

def check_effis():
    """Verify EFFIS TIFFs are reachable (actual reading in step 2)."""
    for year, url in EFFIS_TIFF_URLS.items():
        r = SESSION.head(url, timeout=20, allow_redirects=True)
        mb = int(r.headers.get("content-length", 0)) // 1048576
        status = "✓" if r.status_code == 200 else "✗"
        print(f"  [EFFIS] severity_{year}.tiff {status} {mb} MB  →  {r.url[:60]}")
    # Save URLs for step 2
    (RAW / "effis_tiff_urls.json").write_text(json.dumps(EFFIS_TIFF_URLS))

# ── 3. Nominatim: fire stations + civil protection ─────────────────────────
NOM_URL  = "https://nominatim.openstreetmap.org/search"
NOM_RATE = 1.1   # seconds between requests (Nominatim policy: max 1 req/s)

def _nominatim_query(amenity, viewbox, bounded=1):
    """Return list of result dicts from Nominatim for a given amenity + bbox."""
    W, S, E, N = viewbox
    params = {
        "amenity":    amenity,
        "countrycodes": "it",
        "viewbox":    f"{W},{N},{E},{S}",   # Nominatim: left,top,right,bottom
        "bounded":    bounded,
        "limit":      50,
        "format":     "json",
        "extratags":  1,
    }
    r = SESSION.get(NOM_URL, params=params, timeout=20)
    r.raise_for_status()
    time.sleep(NOM_RATE)
    return r.json()

def fetch_facilities():
    features = []
    for island, bbox in REGIONS.items():
        for amenity in ["fire_station", "civil_protection"]:
            print(f"  [OSM] {island} {amenity} …", end=" ", flush=True)
            try:
                results = _nominatim_query(amenity, bbox)
                for item in results:
                    features.append({
                        "type": "Feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [float(item["lon"]), float(item["lat"])],
                        },
                        "properties": {
                            "island":   island,
                            "amenity":  amenity,
                            "name":     item.get("name", ""),
                            "osm_id":   item.get("osm_id"),
                        },
                    })
                print(f"{len(results)} found")
            except Exception as exc:
                print(f"FAILED ({exc})")

    # Also query with office=civil_protection
    for island, bbox in REGIONS.items():
        for q_term in ["protezione civile"]:
            print(f"  [OSM] {island} '{q_term}' freetext …", end=" ", flush=True)
            W, S, E, N = bbox
            params = {
                "q": q_term, "countrycodes": "it",
                "viewbox": f"{W},{N},{E},{S}", "bounded": 1,
                "limit": 50, "format": "json", "extratags": 1,
            }
            try:
                r = SESSION.get(NOM_URL, params=params, timeout=20)
                r.raise_for_status()
                time.sleep(NOM_RATE)
                for item in r.json():
                    if any(k in item.get("type","").lower() for k in ["civil","emergency","protection"]) \
                       or "protezione" in item.get("name","").lower():
                        features.append({
                            "type": "Feature",
                            "geometry": {"type": "Point",
                                         "coordinates": [float(item["lon"]), float(item["lat"])]},
                            "properties": {"island": island, "amenity": "civil_protection",
                                           "name": item.get("name",""), "osm_id": item.get("osm_id")},
                        })
                print(f"{len(r.json())} found")
            except Exception as exc:
                print(f"FAILED ({exc})")

    fc = {"type": "FeatureCollection", "features": features}
    out = RAW / "facilities.geojson"
    out.write_text(json.dumps(fc, indent=2))
    print(f"  [OSM] Total facilities saved: {len(features)} → {out.name}")

# ── 4. Nominatim: municipality population tags ─────────────────────────────
def _get_muni_population(name, island):
    """Query Nominatim for a single municipality name; return population int or None."""
    W, S, E, N = REGIONS[island]
    params = {
        "q":           name,
        "countrycodes": "it",
        "viewbox":     f"{W},{N},{E},{S}",
        "bounded":     1,
        "limit":       1,
        "format":      "json",
        "extratags":   1,
        "addressdetails": 0,
    }
    try:
        r = SESSION.get(NOM_URL, params=params, timeout=15)
        r.raise_for_status()
        time.sleep(NOM_RATE)
        items = r.json()
        if items:
            pop = items[0].get("extratags", {}).get("population")
            if pop:
                return int(str(pop).replace(",","").replace(".",""))
    except Exception:
        pass
    return None

def fetch_population(islands_geojson: dict):
    """
    For each municipality try to get OSM population tag.
    Saves a dict {GID_3: population} as JSON.
    Rate-limited; will take ~15 min for 767 municipalities.
    Caches partial results so it can resume if interrupted.
    """
    cache_path = RAW / "population_cache.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}

    features = islands_geojson["features"]
    total = len(features)
    found = 0

    for i, feat in enumerate(features):
        gid  = feat["properties"]["GID_3"]
        name = feat["properties"]["NAME_3"]
        island = "sicily" if feat["properties"]["GID_1"] == REGION_GIDS["sicily"] else "sardinia"

        if gid in cache:
            if cache[gid] is not None:
                found += 1
            continue

        pop = _get_muni_population(name, island)
        cache[gid] = pop
        if pop:
            found += 1

        if i % 20 == 0 or i == total - 1:
            cache_path.write_text(json.dumps(cache))
            pct = (i+1)/total*100
            print(f"  [POP] {i+1}/{total} ({pct:.0f}%) — {found} with population data", end="\r")

    cache_path.write_text(json.dumps(cache))
    print(f"\n  [POP] Done: {found}/{total} municipalities have OSM population tag")
    return cache

# ── main ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n=== Step 1: Data Acquisition ===\n")

    print("1/4  Municipality boundaries (GADM)")
    fetch_gadm()
    filter_gadm()

    print("\n2/4  EFFIS fire severity TIFFs")
    check_effis()

    print("\n3/4  Emergency facilities (Nominatim/OSM)")
    fetch_facilities()

    print("\n4/4  Municipality population (OSM extratags)")
    # Merge both islands into one GeoJSON for iteration
    all_feats = []
    for island in REGION_GIDS:
        fc = json.loads((RAW / f"gadm_{island}.geojson").read_text())
        all_feats.extend(fc["features"])
    combined = {"type": "FeatureCollection", "features": all_feats}
    pop_cache = fetch_population(combined)

    print("\n=== Acquisition complete ===")
    print(f"  Raw files in: {RAW}")
