"""
Wildfire data acquisition for Mediterranean islands.

Sources:
  1. NASA FIRMS  – MODIS C6.1, VIIRS S-NPP C2, VIIRS NOAA-20 C2 (NRT, public)
  2. NASA EONET  – Wildfire events with area (hectares) as severity proxy
     (public API, historical back to ~2000 via GDACS/InciWeb)

Regions covered:
  Sicily        36–38 N, 12–16 E
  Sardinia      38–42 N,  8–10 E
  Corsica       41–43 N,  8–10 E
  Greek islands 34–38 N, 23–28 E
"""

import csv
import io
import json
import time
from datetime import date, timedelta
from pathlib import Path

import requests

OUT = Path(__file__).parent

REGIONS = {
    "sicily":        {"west": 12, "south": 36, "east": 16, "north": 38},
    "sardinia":      {"west":  8, "south": 38, "east": 10, "north": 42},
    "corsica":       {"west":  8, "south": 41, "east": 10, "north": 43},
    "greek_islands": {"west": 23, "south": 34, "east": 28, "north": 38},
}

# ---------------------------------------------------------------------------
# 1. NASA FIRMS – NRT 7-day Europe CSVs (no API key required)
# ---------------------------------------------------------------------------
FIRMS_BASE = "https://firms.modaps.eosdis.nasa.gov/data/active_fire"
FIRMS_PRODUCTS = [
    ("MODIS_C6.1",     f"{FIRMS_BASE}/modis-c6.1/csv/MODIS_C6_1_Europe_7d.csv"),
    ("VIIRS_SNPP_C2",  f"{FIRMS_BASE}/suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_Europe_7d.csv"),
    ("VIIRS_NOAA20_C2",f"{FIRMS_BASE}/noaa-20-viirs-c2/csv/J1_VIIRS_C2_Europe_7d.csv"),
]


def in_region(lat: float, lon: float, region: dict) -> bool:
    return (region["south"] <= lat <= region["north"] and
            region["west"]  <= lon <= region["east"])


def region_for(lat: float, lon: float) -> str | None:
    for name, bbox in REGIONS.items():
        if in_region(lat, lon, bbox):
            return name
    return None


def fetch_firms_nrt() -> list[dict]:
    """Download FIRMS NRT CSVs and filter to the four island regions."""
    rows = []
    for product, url in FIRMS_PRODUCTS:
        print(f"  Fetching FIRMS {product} …", end=" ", flush=True)
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            reader = csv.DictReader(io.StringIO(r.text))
            count = 0
            for rec in reader:
                lat = float(rec["latitude"])
                lon = float(rec["longitude"])
                region = region_for(lat, lon)
                if region is None:
                    continue
                # unify column names across MODIS/VIIRS
                brightness = rec.get("brightness") or rec.get("bright_ti4", "")
                bright_bg  = rec.get("bright_t31") or rec.get("bright_ti5", "")
                rows.append({
                    "source":     "FIRMS_NRT",
                    "product":    product,
                    "region":     region,
                    # --- target fields ---
                    "latitude":   lat,
                    "longitude":  lon,
                    "acq_date":   rec["acq_date"],
                    "brightness": brightness,   # K  (MODIS=channel 21/22; VIIRS=I-band Ti4)
                    "frp":        rec.get("frp", ""),  # MW – Fire Radiative Power = severity
                    "confidence": rec["confidence"],   # 0–100 (MODIS) or low/nominal/high (VIIRS)
                })
                count += 1
            print(f"{count} detections")
        except Exception as exc:
            print(f"FAILED ({exc})")
    return rows


# ---------------------------------------------------------------------------
# 2. NASA EONET – historical wildfire events (public, no auth)
# ---------------------------------------------------------------------------
EONET_URL = "https://eonet.gsfc.nasa.gov/api/v3/events"

# Combined bbox that covers all four regions  west,south,east,north
COMBINED_BBOX = "8,34,28,43"

# Earliest year with reasonable EONET coverage
START_DATE = "2000-01-01"
END_DATE   = date.today().isoformat()


def fetch_eonet_wildfires() -> list[dict]:
    """Fetch all EONET wildfire events in the Mediterranean island bbox."""
    rows = []
    params = {
        "category": "wildfires",
        "status":   "all",
        "bbox":     COMBINED_BBOX,
        "start":    START_DATE,
        "end":      END_DATE,
        "limit":    2500,
    }
    page = 1
    while True:
        print(f"  Fetching EONET page {page} …", end=" ", flush=True)
        try:
            r = requests.get(EONET_URL, params=params, timeout=60)
            r.raise_for_status()
            data = r.json()
            events = data.get("events", [])
            print(f"{len(events)} events")
            if not events:
                break
            for evt in events:
                for geom in evt.get("geometry", []):
                    coords = geom.get("coordinates")
                    if not coords or geom.get("type") != "Point":
                        continue
                    lon, lat = coords[0], coords[1]
                    region = region_for(lat, lon)
                    # Keep events inside our specific regions only
                    if region is None:
                        continue
                    rows.append({
                        "source":    "EONET",
                        "product":   "EONET_GDACS",
                        "region":    region,
                        # --- target fields ---
                        "latitude":  lat,
                        "longitude": lon,
                        "acq_date":  geom.get("date", "")[:10],  # ISO date only
                        "brightness": "",        # not available in EONET
                        "frp":       "",         # not available in EONET
                        "confidence": "",        # not available in EONET
                        # extra context kept for the dashboard
                        "area_ha":   geom.get("magnitudeValue", ""),  # severity: burned area (ha)
                        "title":     evt.get("title", ""),
                        "closed":    evt.get("closed", "")[:10] if evt.get("closed") else "",
                    })
            # EONET doesn't paginate with page number; break after first batch
            break
        except Exception as exc:
            print(f"FAILED ({exc})")
            break
    return rows


# ---------------------------------------------------------------------------
# 3. Save results
# ---------------------------------------------------------------------------
def save_csv(rows: list[dict], path: Path, fieldnames: list[str] | None = None) -> None:
    if not rows:
        print(f"  No data to save → {path.name}")
        return
    fields = fieldnames or list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Saved {len(rows):,} rows → {path.name}")


def save_json(rows: list[dict], path: Path) -> None:
    if not rows:
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    print(f"  Saved {len(rows):,} records → {path.name}")


def print_summary(label: str, rows: list[dict], date_field: str = "acq_date") -> None:
    if not rows:
        print(f"\n{label}: 0 records")
        return
    by_region: dict[str, int] = {}
    dates = []
    for r in rows:
        by_region[r["region"]] = by_region.get(r["region"], 0) + 1
        d = r.get(date_field, "")
        if d:
            dates.append(d[:10])
    print(f"\n{'─'*50}")
    print(f"{label}: {len(rows):,} records total")
    for reg, n in sorted(by_region.items()):
        print(f"  {reg:<18}: {n:>5}")
    if dates:
        print(f"  date range     : {min(dates)} → {max(dates)}")
    print(f"{'─'*50}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("\n=== NASA FIRMS NRT (7-day, no auth required) ===")
    firms_rows = fetch_firms_nrt()
    save_csv(firms_rows, OUT / "firms_nrt_mediterranean.csv")
    save_json(firms_rows, OUT / "firms_nrt_mediterranean.json")
    print_summary("FIRMS NRT", firms_rows, "acq_date")

    print("\n=== NASA EONET Wildfire Events (historical, no auth) ===")
    eonet_rows = fetch_eonet_wildfires()
    save_csv(eonet_rows, OUT / "eonet_wildfires_mediterranean.csv")
    save_json(eonet_rows, OUT / "eonet_wildfires_mediterranean.json")
    print_summary("EONET Wildfires", eonet_rows, "date")

    # -----------------------------------------------------------------------
    # Combined catalogue (merge both sources into one GeoJSON-ready CSV)
    # -----------------------------------------------------------------------
    # Combined catalogue: only the five target fields + source context
    combined = []
    COMBINED_FIELDS = [
        "source", "product", "region",
        "latitude", "longitude", "acq_date",
        "brightness", "frp", "confidence",
        "area_ha", "title",
    ]
    for r in firms_rows:
        combined.append({
            "source":     r["source"],
            "product":    r["product"],
            "region":     r["region"],
            "latitude":   r["latitude"],
            "longitude":  r["longitude"],
            "acq_date":   r["acq_date"],
            "brightness": r["brightness"],
            "frp":        r["frp"],
            "confidence": r["confidence"],
            "area_ha":    "",
            "title":      "",
        })
    for r in eonet_rows:
        combined.append({
            "source":     r["source"],
            "product":    r["product"],
            "region":     r["region"],
            "latitude":   r["latitude"],
            "longitude":  r["longitude"],
            "acq_date":   r["acq_date"],
            "brightness": "",
            "frp":        "",
            "confidence": "",
            "area_ha":    r["area_ha"],
            "title":      r["title"],
        })

    save_csv(combined, OUT / "wildfire_mediterranean_combined.csv",
             fieldnames=COMBINED_FIELDS)
    print_summary("COMBINED", combined, "date")

    print("\nDone. Output files written to:", OUT)
