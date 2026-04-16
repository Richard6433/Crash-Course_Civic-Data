"""
Step 2 — Processing + Priority Flagging (v2 — improved data)

Inputs (from raw/):
  gadm_sicily.geojson                   — municipality boundaries (Sicily only)
  effis_tiff_urls.json                  — URLs for EFFIS severity rasters 2018-2024
  facilities.geojson                    — emergency facilities (civil protection filtered)
  istat/sicily_elderly_by_istat_code.json — ISTAT 65+ share per municipality (2026 estimate)
  istat/gadm_to_istat.json              — GADM GID_3 → ISTAT commune code mapping

Outputs (to processed/):
  municipalities_priority.geojson  — 391 Sicily municipalities
  municipalities_priority.csv

Fields in output:
  GID_3, NAME_3, province
  centroid_lat, centroid_lon, area_km2
  fire_years_2018_2024  — count of years (out of 2018-2024) with EFFIS fire pixels
  elderly_pct           — share of population aged 65+ (ISTAT per municipality)
  dist_civil_prot_km    — km to nearest civil protection station (OSM)
  priority              — True if ALL three conditions below are met

Priority flag (all three must be true):
  1. fire_years_2018_2024 >= 3   (burned ≥3 times out of 7 years 2018-2024)
  2. elderly_pct > 0.235         (above Italy national average)
  3. dist_civil_prot_km > 20     (>20 km from nearest civil protection)

Elderly data: ISTAT "Popolazione residente per età e sesso" (demo.istat.it),
  January 1, 2026 estimate. Ages summed for 65-100, denominator from age=999 row.
  Source: https://demo.istat.it/data/posas/POSAS_2026_it_Comuni.zip
"""

import json
import math
import os
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import from_bounds

RAW       = Path(__file__).parent / "raw"
PROCESSED = Path(__file__).parent / "processed"
PROCESSED.mkdir(exist_ok=True)

ITALY_NATIONAL_ELDERLY = 0.235  # priority threshold (Italy national average)

# ── EFFIS years to process for fire frequency indicator ───────────────────────
FIRE_COUNT_YEARS = [2018, 2019, 2020, 2021, 2022, 2023, 2024]
FIRE_MIN_YEARS   = 3   # priority condition: burned in ≥ N of these 7 years


# ── helpers ───────────────────────────────────────────────────────────────────
def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


# ── 1. Load Sicily municipality boundaries ────────────────────────────────────
def load_municipalities():
    gdf = gpd.read_file(RAW / "gadm_sicily.geojson")
    gdf = gdf.set_crs("EPSG:4326")
    gdf["centroid_lat"] = gdf.geometry.centroid.y
    gdf["centroid_lon"] = gdf.geometry.centroid.x
    gdf_ea = gdf.to_crs("ESRI:54009")
    gdf["area_km2"] = gdf_ea.geometry.area / 1e6
    gdf["province"] = gdf["NAME_2"]
    print(f"  Loaded {len(gdf)} Sicily municipalities across "
          f"{gdf['province'].nunique()} provinces")
    return gdf


# ── 2. EFFIS fire exposure ────────────────────────────────────────────────────
EFFIS_TIFF_URLS = json.loads((RAW / "effis_tiff_urls.json").read_text())

SICILY_W, SICILY_S, SICILY_E, SICILY_N = 12.0, 36.0, 16.0, 38.5


def _download_clip_tiff(url, year):
    """Download a single EFFIS severity TIFF clipped to Sicily bbox."""
    out = RAW / "effis" / f"effis_sev_{year}_clip.tif"
    out.parent.mkdir(exist_ok=True)
    if out.exists():
        print(f"  [EFFIS {year}] cached → {out.name}")
        return out

    os.environ.setdefault("GDAL_HTTP_UNSAFESSL", "YES")
    vsi = f"/vsicurl/{url}"
    print(f"  [EFFIS {year}] downloading clip …", end=" ", flush=True)
    try:
        with rasterio.Env(GDAL_HTTP_UNSAFESSL="YES"):
            with rasterio.open(vsi) as src:
                window = from_bounds(
                    SICILY_W, SICILY_S, SICILY_E, SICILY_N, src.transform
                )
                window = window.intersection(
                    rasterio.windows.Window(0, 0, src.width, src.height)
                )
                data = src.read(1, window=window)
                transform = src.window_transform(window)
                profile = src.profile.copy()
                profile.update(
                    driver="GTiff", width=window.width, height=window.height,
                    transform=transform, compress="lzw",
                )
        with rasterio.open(out, "w", **profile) as dst:
            dst.write(data, 1)
        kb = out.stat().st_size // 1024
        print(f"done ({kb} KB)")
        return out
    except Exception as exc:
        print(f"FAILED ({exc})")
        return None


def _frac_burned_per_muni(local_tif, munis):
    """Fraction of pixels burned (value > 0) per municipality via zonal stats."""
    from rasterstats import zonal_stats
    stats = zonal_stats(
        munis,
        str(local_tif),
        stats=[],
        add_stats={"burned": lambda a: float(
            (a.compressed() > 0).sum() / max(a.count(), 1)
        )},
        nodata=0,
    )
    return [s.get("burned") or 0.0 for s in stats]


def compute_fire_years(munis):
    """For each year in FIRE_COUNT_YEARS, flag whether municipality had fire pixels."""
    frac_cols = []
    for year in FIRE_COUNT_YEARS:
        url   = EFFIS_TIFF_URLS.get(str(year))
        local = _download_clip_tiff(url, year) if url else None
        col   = f"frac_{year}"

        if local and local.exists():
            print(f"  [EFFIS {year}] zonal stats for {len(munis)} municipalities …",
                  end=" ", flush=True)
            fracs = _frac_burned_per_muni(local, munis)
            munis[col] = fracs
            n = (munis[col] > 0).sum()
            print(f"done  ({n} with fire pixels)")
        else:
            munis[col] = 0.0
            print(f"  [EFFIS {year}] skipped (no TIFF)")
        frac_cols.append(col)

    munis["fire_years_2018_2024"] = sum(
        (munis[fc] > 0).astype(int) for fc in frac_cols
    )
    dist = munis["fire_years_2018_2024"].value_counts().sort_index().to_dict()
    print(f"  [EFFIS] fire_years_2018_2024 distribution: {dist}")
    return munis


# ── 3. Distance to nearest civil protection station ───────────────────────────
def compute_civil_prot_distance(munis):
    fac = gpd.read_file(RAW / "facilities.geojson")
    cp = fac[
        (fac["amenity"] == "civil_protection") &
        (fac["island"] == "sicily")
    ].drop_duplicates(subset="osm_id")

    if cp.empty:
        print("  [CIVPROT] No civil protection facilities — distance = 999 km")
        munis["dist_civil_prot_km"] = 999.0
        return munis

    cp_pts = [(r.geometry.y, r.geometry.x) for _, r in cp.iterrows()]
    print(f"  [CIVPROT] {len(cp_pts)} stations; computing distances …",
          end=" ", flush=True)

    dists = []
    for _, row in munis.iterrows():
        clat, clon = row["centroid_lat"], row["centroid_lon"]
        min_d = min(haversine_km(clat, clon, flat, flon) for flat, flon in cp_pts)
        dists.append(round(min_d, 2))

    munis["dist_civil_prot_km"] = dists
    print(f"done  (median {np.median(dists):.1f} km, max {max(dists):.1f} km)")
    return munis


# ── 4. Elderly population share (ISTAT per municipality) ─────────────────────
def assign_elderly_pct(munis):
    """Use real ISTAT municipality-level 65+ share."""
    istat = json.loads((RAW / "istat" / "sicily_elderly_by_istat_code.json").read_text())
    mapping = json.loads((RAW / "istat" / "gadm_to_istat.json").read_text())

    shares = []
    for _, row in munis.iterrows():
        gid3 = row["GID_3"]
        istat_code = mapping.get(gid3)
        if istat_code and istat_code in istat:
            shares.append(istat[istat_code]["share"])
        else:
            print(f"  [ELDERLY] WARNING: no ISTAT match for {row['NAME_3']} ({gid3})"
                  f" — using national average")
            shares.append(ITALY_NATIONAL_ELDERLY)

    munis["elderly_pct"] = shares
    above = sum(1 for s in shares if s > ITALY_NATIONAL_ELDERLY)
    print(f"  [ELDERLY] {above}/{len(shares)} municipalities above national avg 23.5%")
    print(f"  [ELDERLY] Range: {min(shares):.1%} – {max(shares):.1%}, "
          f"median: {sorted(shares)[len(shares)//2]:.1%}")
    return munis


# ── 5. Priority flag ──────────────────────────────────────────────────────────
def apply_priority_flag(munis):
    c1 = munis["fire_years_2018_2024"] >= FIRE_MIN_YEARS
    c2 = munis["elderly_pct"] > ITALY_NATIONAL_ELDERLY
    c3 = munis["dist_civil_prot_km"] > 20.0

    munis["priority"] = c1 & c2 & c3
    n = munis["priority"].sum()
    print(f"  [PRIORITY] {n}/{len(munis)} municipalities flagged as priority")
    print(f"    fire >= {FIRE_MIN_YEARS} years (of 7):  {c1.sum():>3}")
    print(f"    elderly > 23.5%:        {c2.sum():>3}")
    print(f"    dist civil prot > 20km: {c3.sum():>3}")
    print(f"    ALL three (priority):   {n:>3}")
    return munis


# ── 6. Export ─────────────────────────────────────────────────────────────────
KEEP_COLS = [
    "GID_3", "NAME_3", "province",
    "centroid_lat", "centroid_lon", "area_km2",
    "fire_years_2018_2024",
    "elderly_pct",
    "dist_civil_prot_km",
    "priority",
    "geometry",
]


def export(munis):
    out_cols = [c for c in KEEP_COLS if c in munis.columns]
    out = munis[out_cols].copy()
    out["priority"] = out["priority"].astype(bool)
    out_path = PROCESSED / "municipalities_priority.geojson"
    out.to_file(out_path, driver="GeoJSON")
    print(f"  [OUT] {len(out)} features → {out_path}")

    csv_cols = [c for c in out_cols if c != "geometry"]
    out[csv_cols].to_csv(PROCESSED / "municipalities_priority.csv", index=False)
    print(f"  [OUT] CSV → {PROCESSED / 'municipalities_priority.csv'}")


# ── main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n=== Step 2: Processing + Priority Flagging (v2) ===\n")

    print("1/5  Loading municipality boundaries")
    munis = load_municipalities()

    print(f"\n2/5  Fire exposure (EFFIS {FIRE_COUNT_YEARS[0]}–{FIRE_COUNT_YEARS[-1]})")
    munis = compute_fire_years(munis)

    print("\n3/5  Distance to nearest civil protection station")
    munis = compute_civil_prot_distance(munis)

    print("\n4/5  Elderly population share (ISTAT per municipality)")
    munis = assign_elderly_pct(munis)

    print("\n5/5  Priority flag")
    munis = apply_priority_flag(munis)

    export(munis)
    print("\n=== Processing complete ===")
