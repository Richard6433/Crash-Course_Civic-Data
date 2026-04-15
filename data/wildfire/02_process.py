"""
Step 2 — Processing + Vulnerability Scoring

Inputs  (from raw/):
  gadm_sicily.geojson, gadm_sardinia.geojson   — municipality boundaries
  effis_tiff_urls.json                           — URLs for severity rasters
  facilities.geojson                             — fire stations + civil protection
  population_cache.json                          — OSM population tags

Outputs (to processed/):
  municipalities_scored.geojson   — one feature per municipality with all indicators
                                    + final vulnerability class (low/medium/high)

Score components:
  fire_score        (50 %)  = normalised mean EFFIS severity across 2021 + 2023
  access_score      (30 %)  = normalised straight-line km to nearest facility (inverted)
  pop_density_score (20 %)  = normalised population density (people/km²)
"""

import json, math, os
from pathlib import Path

import numpy as np
import geopandas as gpd
import rasterio
from rasterio.windows import from_bounds
from shapely.geometry import shape, Point
import pandas as pd

RAW       = Path(__file__).parent / "raw"
PROCESSED = Path(__file__).parent / "processed"
PROCESSED.mkdir(exist_ok=True)

# Italy national elderly share (2021 census) — used uniformly
# Source: ISTAT, Annuario Statistico Italiano 2022
ITALY_ELDERLY_PCT = 0.235

# Median municipal population for municipalities where OSM has no tag
ITALY_MEDIAN_MUNI_POP = 2_500   # approx. median for small Italian comuni

# ── helpers ───────────────────────────────────────────────────────────────
def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def minmax(series):
    lo, hi = series.min(), series.max()
    if hi == lo:
        return pd.Series(0.5, index=series.index)
    return (series - lo) / (hi - lo)


# ── 1. Load municipality boundaries ──────────────────────────────────────
def load_municipalities():
    gdfs = []
    for island in ("sicily",):          # Sardinia removed per scope decision
        gdf = gpd.read_file(RAW / f"gadm_{island}.geojson")
        gdf["island"] = island
        gdfs.append(gdf)
    munis = pd.concat(gdfs, ignore_index=True)
    munis = munis.set_crs("EPSG:4326")
    munis["centroid_lat"] = munis.geometry.centroid.y
    munis["centroid_lon"] = munis.geometry.centroid.x
    # area in km²  (reproject to equal-area for accuracy)
    munis_ea = munis.to_crs("ESRI:54009")
    munis["area_km2"] = munis_ea.geometry.area / 1e6
    print(f"  Loaded {len(munis)} municipalities "
          f"({len(munis[munis.island=='sicily'])} Sicily, "
          f"{len(munis[munis.island=='sardinia'])} Sardinia)")
    return munis


# ── 2. Fire exposure from EFFIS severity rasters ──────────────────────────
EFFIS_TIFF_URLS = json.loads((RAW / "effis_tiff_urls.json").read_text())

def _download_clip_tiff(url, year, west=8.0, south=36.0, east=16.0, north=42.0):
    """
    Download a single clipped window covering Sicily+Sardinia and save locally.
    Makes ONE HTTP range request per year instead of one per municipality.
    """
    out = RAW / "effis" / f"effis_sev_{year}_clip.tif"
    out.parent.mkdir(exist_ok=True)
    if out.exists():
        print(f"  [EFFIS {year}] already cached → {out.name}")
        return out

    os.environ.setdefault("GDAL_HTTP_UNSAFESSL", "YES")
    vsi = f"/vsicurl/{url}"
    print(f"  [EFFIS {year}] downloading clip (8–16°E, 36–42°N) …", end=" ", flush=True)
    try:
        with rasterio.Env(GDAL_HTTP_UNSAFESSL="YES"):
            with rasterio.open(vsi) as src:
                window = from_bounds(west, south, east, north, src.transform)
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


def _severity_stats_per_muni(local_tif, munis):
    """
    Compute mean severity and fraction burned per municipality from a local TIFF.
    Uses rasterstats zonal_stats for efficiency.
    """
    from rasterstats import zonal_stats
    nodata = 0
    stats = zonal_stats(
        munis,
        str(local_tif),
        stats=["mean", "count"],
        add_stats={"burned": lambda a: float((a.compressed() > 0).sum() / max(a.count(), 1))},
        nodata=nodata,
    )
    sev  = [s.get("mean") or 0.0 for s in stats]
    frac = [s.get("burned") or 0.0 for s in stats]
    return sev, frac


def compute_fire_exposure(munis):
    """Download clipped EFFIS TIFFs (one per year) then run zonal stats per municipality."""
    years = sorted(EFFIS_TIFF_URLS.keys())
    for year in years:
        url  = EFFIS_TIFF_URLS[year]
        local = _download_clip_tiff(url, year)

        col_sev  = f"sev_{year}"
        col_frac = f"frac_{year}"

        if local and local.exists():
            print(f"  [EFFIS {year}] computing zonal stats for {len(munis)} municipalities …",
                  end=" ", flush=True)
            sev, frac = _severity_stats_per_muni(local, munis)
            munis[col_sev]  = sev
            munis[col_frac] = frac
            n_with_fire = (munis[col_frac] > 0).sum()
            print(f"done  ({n_with_fire} municipalities with fire pixels)")
        else:
            munis[col_sev]  = 0.0
            munis[col_frac] = 0.0
            print(f"  [EFFIS {year}] skipped (download failed)")

    sev_cols  = [f"sev_{y}"  for y in years]
    frac_cols = [f"frac_{y}" for y in years]
    munis["fire_severity_mean"] = munis[sev_cols].mean(axis=1)
    munis["fire_frac_mean"]     = munis[frac_cols].mean(axis=1)
    munis["fire_years_affected"] = sum((munis[fc] > 0).astype(int) for fc in frac_cols)
    print(f"  [EFFIS] fire_years_affected: {munis['fire_years_affected'].value_counts().to_dict()}")
    return munis


# ── 3. Distance to nearest emergency facility ──────────────────────────────
def compute_access(munis):
    fac = gpd.read_file(RAW / "facilities.geojson")
    if fac.empty:
        print("  [ACCESS] No facilities found — access score will be 0 for all")
        munis["dist_facility_km"] = 999.0
        return munis

    fac_pts = [(r.geometry.y, r.geometry.x) for _, r in fac.iterrows()]
    print(f"  [ACCESS] {len(fac_pts)} facilities; computing distances …", end=" ", flush=True)

    dists = []
    for _, row in munis.iterrows():
        clat, clon = row["centroid_lat"], row["centroid_lon"]
        min_d = min(haversine_km(clat, clon, flat, flon) for flat, flon in fac_pts)
        dists.append(min_d)
    munis["dist_facility_km"] = dists
    print(f"done  (median {np.median(dists):.1f} km, max {max(dists):.1f} km)")
    return munis


# ── 4. Demographics ────────────────────────────────────────────────────────
def compute_demographics(munis):
    pop_cache = json.loads((RAW / "population_cache.json").read_text()) \
                if (RAW / "population_cache.json").exists() else {}

    pops = []
    for _, row in munis.iterrows():
        gid = row["GID_3"]
        pop = pop_cache.get(gid)
        pops.append(int(pop) if pop else ITALY_MEDIAN_MUNI_POP)

    munis["population"]       = pops
    munis["pop_density_km2"]  = munis["population"] / munis["area_km2"].clip(lower=0.01)
    munis["elderly_pct"]      = ITALY_ELDERLY_PCT    # uniform national average

    n_real = sum(1 for p in pops if p != ITALY_MEDIAN_MUNI_POP)
    print(f"  [DEMO] {n_real}/{len(munis)} municipalities with real OSM population tag")
    print(f"  [DEMO] Elderly % = {ITALY_ELDERLY_PCT*100:.1f}% national average (uniform)")
    return munis


# ── 5. Vulnerability scoring ───────────────────────────────────────────────
def score(munis):
    # Normalise each indicator 0–1
    munis["n_fire"]    = minmax(munis["fire_severity_mean"] * 0.6 +
                                munis["fire_frac_mean"]     * 0.4 +
                                munis["fire_years_affected"] * 0.3)
    # Access: longer distance = higher vulnerability → invert after norm
    munis["n_access"]  = 1 - minmax(munis["dist_facility_km"])
    munis["n_density"] = minmax(munis["pop_density_km2"])

    # Weights
    munis["score_raw"] = (
        0.50 * munis["n_fire"] +
        0.30 * munis["n_access"] +
        0.20 * munis["n_density"]
    )

    # Classify into thirds
    lo = munis["score_raw"].quantile(0.33)
    hi = munis["score_raw"].quantile(0.67)
    munis["risk_class"] = pd.cut(
        munis["score_raw"],
        bins=[-0.001, lo, hi, 1.001],
        labels=["low", "medium", "high"]
    )

    print(f"  [SCORE] Distribution: {munis['risk_class'].value_counts().to_dict()}")
    return munis


# ── 6. Export ──────────────────────────────────────────────────────────────
KEEP_COLS = [
    "GID_3", "NAME_3", "island",
    "centroid_lat", "centroid_lon", "area_km2",
    # Fire
    "fire_severity_mean", "fire_frac_mean", "fire_years_affected",
    # Access
    "dist_facility_km",
    # Demographics
    "population", "pop_density_km2", "elderly_pct",
    # Score
    "n_fire", "n_access", "n_density", "score_raw", "risk_class",
    "geometry",
]

def export(munis):
    out_cols = [c for c in KEEP_COLS if c in munis.columns]
    out = munis[out_cols].copy()
    out["risk_class"] = out["risk_class"].astype(str)
    out_path = PROCESSED / "municipalities_scored.geojson"
    out.to_file(out_path, driver="GeoJSON")
    print(f"  [OUT] {len(out)} features → {out_path}")

    # Also write a CSV for spreadsheet users
    csv_cols = [c for c in out_cols if c != "geometry"]
    out[csv_cols].to_csv(PROCESSED / "municipalities_scored.csv", index=False)
    print(f"  [OUT] CSV → {PROCESSED / 'municipalities_scored.csv'}")


# ── main ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n=== Step 2: Processing + Scoring ===\n")

    print("1/5  Loading municipality boundaries")
    munis = load_municipalities()

    print("\n2/5  Fire exposure (EFFIS severity rasters)")
    munis = compute_fire_exposure(munis)

    print("\n3/5  Emergency access (nearest facility)")
    munis = compute_access(munis)

    print("\n4/5  Demographics")
    munis = compute_demographics(munis)

    print("\n5/5  Scoring + classification")
    munis = score(munis)

    export(munis)
    print("\n=== Processing complete ===")
