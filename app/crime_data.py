"""
District-level crime risk.

Unlike every other feature in this system, crime statistics are not
available from a free, keyless API. NCRB's *Crime in India* district tables
and the Delhi Police annual reports are published as PDFs, and
data.gov.in's equivalents require a registered key. So this layer is
built as a **drop-in file** rather than a fetcher:
`data/delhi_district_crime.csv` maps each Delhi district to a reported
crime volume, and this module turns that into a per-grid-cell
`crime_risk_index` using real district boundaries from OpenStreetMap.

The important property is that the system never *invents* crime numbers.
The shipped CSV is a schema template whose rows are marked
`source=PLACEHOLDER`. While that is the case:

* `load_district_crime()` reports `available=False`,
* the training pipeline drops `crime_risk_index` from the feature set
  entirely and redistributes its composite weight over the measured
  features, and
* `/health`, the model card and the frontend all say crime data is not
  configured.

The model shipped by default is therefore trained *only* on measured
OpenStreetMap quantities. Filling in the CSV is a strict upgrade that the
pipeline picks up on the next `python train_model.py`, with no code change.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.config import (
    DISTRICT_CRIME_PATH,
    GRID_CELLS_PER_SIDE,
    OSM_CACHE_DIR,
)
from app.geo import area_code_from_indices, cell_center

log = logging.getLogger(__name__)

PLACEHOLDER_SOURCE = "PLACEHOLDER"

REQUIRED_COLUMNS = ("district", "year", "source", "reported_crimes", "population")

# Delhi's eleven revenue districts. Used to validate the CSV and to request
# the matching boundary polygons from OpenStreetMap.
DELHI_DISTRICTS = (
    "Central Delhi",
    "East Delhi",
    "New Delhi",
    "North Delhi",
    "North East Delhi",
    "North West Delhi",
    "Shahdara",
    "South Delhi",
    "South East Delhi",
    "South West Delhi",
    "West Delhi",
)


@dataclass
class DistrictCrime:
    """Loaded district crime table plus its provenance."""

    available: bool
    source: str
    year: str
    table: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def description(self) -> str:
        if not self.available:
            return "not configured (see data/README.md)"
        return f"{self.source} ({self.year})"


def load_district_crime(path=DISTRICT_CRIME_PATH) -> DistrictCrime:
    """
    Read `data/delhi_district_crime.csv` and normalise it into a per-district
    risk index in [0, 1].

    A row counts as real only if its `source` is not PLACEHOLDER and it
    carries a positive `reported_crimes` count. If no row qualifies, the
    result is `available=False` and callers must not use crime as a feature.
    """
    if not path.exists():
        log.warning("No district crime file at %s; crime risk disabled.", path)
        return DistrictCrime(available=False, source="none", year="n/a")

    table = pd.read_csv(path)
    missing = [c for c in REQUIRED_COLUMNS if c not in table.columns]
    if missing:
        raise ValueError(f"{path} is missing required column(s): {', '.join(missing)}")

    real = table[
        (table["source"].astype(str).str.strip().str.upper() != PLACEHOLDER_SOURCE)
        & (pd.to_numeric(table["reported_crimes"], errors="coerce").fillna(0) > 0)
    ].copy()

    if real.empty:
        log.warning(
            "%s contains only PLACEHOLDER rows -- crime risk is disabled and the model "
            "will be trained on measured OSM features alone. See data/README.md to "
            "populate it from NCRB / Delhi Police figures.",
            path.name,
        )
        return DistrictCrime(available=False, source=PLACEHOLDER_SOURCE, year="n/a")

    real["reported_crimes"] = pd.to_numeric(real["reported_crimes"])
    real["population"] = pd.to_numeric(real["population"], errors="coerce")

    # Rate per 100,000 residents is the comparable measure; a district is not
    # more dangerous merely for being more populous. Districts with no
    # population figure fall back to the raw count's own scale.
    with np.errstate(divide="ignore", invalid="ignore"):
        rate = np.where(
            real["population"].to_numpy() > 0,
            real["reported_crimes"].to_numpy() / real["population"].to_numpy() * 100_000,
            np.nan,
        )
    if np.isnan(rate).all():
        rate = real["reported_crimes"].to_numpy().astype(float)
    else:
        rate = np.where(np.isnan(rate), np.nanmedian(rate), rate)

    # Min-max across districts, then keep it off the hard 0/1 endpoints: the
    # safest district in Delhi is not a place with zero risk.
    span = rate.max() - rate.min()
    normalised = (rate - rate.min()) / span if span > 0 else np.full_like(rate, 0.5)
    real["crime_risk_index"] = np.round(0.15 + 0.70 * normalised, 4)
    real["crime_rate_per_lakh"] = np.round(rate, 2)

    source = str(real["source"].iloc[0])
    year = str(real["year"].iloc[0])
    log.info("Loaded crime data for %d districts from %s (%s).", len(real), source, year)
    return DistrictCrime(
        available=True,
        source=source,
        year=year,
        table=real[
            ["district", "reported_crimes", "population", "crime_rate_per_lakh", "crime_risk_index"]
        ].reset_index(drop=True),
    )


# ---------------------------------------------------------------------------
# District boundaries -> grid cells
# ---------------------------------------------------------------------------

_BOUNDARY_CACHE = OSM_CACHE_DIR / "district_boundaries.json"

_BOUNDARY_QUERY = """
[out:json][timeout:180];
area["boundary"="administrative"]["admin_level"="4"]["name"="Delhi"]->.delhi;
relation["boundary"="administrative"]["admin_level"="5"](area.delhi);
out geom tags;
"""


def fetch_district_boundaries(refresh: bool = False) -> dict:
    """Download Delhi's district boundary relations from Overpass (cached)."""
    import json

    from app.osm_data import _run_overpass

    if _BOUNDARY_CACHE.exists() and not refresh:
        return json.loads(_BOUNDARY_CACHE.read_text())
    payload = _run_overpass(_BOUNDARY_QUERY)
    _BOUNDARY_CACHE.write_text(json.dumps(payload))
    return payload


def _relation_to_polygon(relation: dict):
    """
    Stitch an OSM boundary relation's outer member ways into a polygon.

    Members arrive as separate, arbitrarily-ordered line fragments, so they
    are merged into closed rings before being polygonised.
    """
    from shapely.geometry import LineString, MultiPolygon
    from shapely.ops import linemerge, polygonize, unary_union

    lines = []
    for member in relation.get("members", []):
        if member.get("role") not in ("outer", ""):
            continue
        geometry = member.get("geometry") or []
        if len(geometry) < 2:
            continue
        lines.append(LineString([(g["lon"], g["lat"]) for g in geometry]))
    if not lines:
        return None
    merged = linemerge(unary_union(lines))
    polygons = list(polygonize(merged))
    if not polygons:
        return None
    return polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)


def build_cell_district_map(refresh: bool = False) -> pd.DataFrame:
    """
    Assign every grid cell to the Delhi district containing its centre.

    Cells whose centre falls outside every district polygon (the bounding
    box overhangs into Haryana and UP) are left unassigned.
    """
    from shapely.geometry import Point

    payload = fetch_district_boundaries(refresh=refresh)
    polygons: list[tuple[str, object]] = []
    for relation in payload.get("elements", []):
        name = (relation.get("tags") or {}).get("name")
        polygon = _relation_to_polygon(relation)
        if name and polygon is not None and not polygon.is_empty:
            polygons.append((name, polygon))

    if not polygons:
        raise ValueError("No Delhi district boundaries returned by Overpass.")
    log.info("Assembled %d district polygons: %s", len(polygons), [n for n, _ in polygons])

    rows = []
    for row_idx in range(GRID_CELLS_PER_SIDE):
        for col_idx in range(GRID_CELLS_PER_SIDE):
            lat, lon = cell_center(row_idx, col_idx)
            point = Point(lon, lat)
            district = next((name for name, poly in polygons if poly.contains(point)), None)
            rows.append(
                {"area_code": area_code_from_indices(row_idx, col_idx), "district": district}
            )
    return pd.DataFrame(rows)


def attach_crime_to_grid(grid: pd.DataFrame, crime: DistrictCrime) -> pd.DataFrame:
    """
    Add `district` and `crime_risk_index` columns to the per-cell grid.

    Returns the grid unchanged (minus a crime column) when crime data is not
    configured, so downstream code can branch on the column's presence.
    """
    if not crime.available:
        return grid

    cell_districts = build_cell_district_map()
    merged = grid.merge(cell_districts, on="area_code", how="left")
    merged = merged.merge(
        crime.table[["district", "crime_risk_index"]], on="district", how="left"
    )
    # Cells outside Delhi proper (the bbox overhangs the NCR border) take the
    # city median rather than being dropped -- routes do cross the boundary.
    merged["crime_risk_index"] = merged["crime_risk_index"].fillna(
        crime.table["crime_risk_index"].median()
    )
    return merged
