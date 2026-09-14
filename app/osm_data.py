"""
Real OpenStreetMap ingestion for the Delhi study area.

This module is what makes SafeHerWay's features *measured* rather than
invented. Everything the model sees about a place -- how densely
streetlights are mapped there, whether the roads carry a `lit=yes` tag,
how much surveillance infrastructure exists, how much walkable footway
there is, how far the nearest metro/bus/hospital/police point is, and how
much day-to-day commercial activity surrounds it -- is aggregated from
OpenStreetMap via the public Overpass API.

Two things matter for using OSM honestly:

1. **Coverage is not uniform.** Central Delhi is mapped in far more detail
   than the outskirts. A cell with zero mapped streetlights is usually an
   under-mapped cell, not an unlit one. Every aggregate is therefore
   returned alongside an `osm_coverage` score, and sparse cells are shrunk
   toward the city-wide mean instead of being read as literal zeros.
2. **It is a snapshot.** Responses are cached under `artifacts/osm_cache/`
   with the Overpass `timestamp_osm_base` recorded, so a training run is
   reproducible and its vintage is auditable.

Nothing here runs at request time -- `fetch_all()` is an offline step
driven by `python fetch_osm_data.py`.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

import httpx
import numpy as np
import pandas as pd

from app.config import (
    DELHI_LAT_RANGE,
    DELHI_LON_RANGE,
    GRID_CELLS_PER_SIDE,
    OSM_CACHE_DIR,
    OVERPASS_ENDPOINTS,
    OVERPASS_TILES_PER_SIDE,
    OVERPASS_TIMEOUT_SECONDS,
    OVERPASS_USER_AGENT,
    POI_CATEGORIES,
)
from app.features import blend_lighting, infra_score_from_components
from app.geo import (
    area_code_from_indices,
    cell_area_km2,
    grid_indices,
    polyline_length_km,
)

log = logging.getLogger(__name__)

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# Layer definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Layer:
    """One Overpass query, plus how its results should be materialised."""

    name: str
    body: str  # `{bbox}` is substituted with the tile's "S,W,N,E"
    out: str  # "center" for point features, "geom" for ways we measure length of


LAYERS: tuple[Layer, ...] = (
    Layer(
        "poi_metro",
        'nwr["station"="subway"]({bbox});'
        'nwr["railway"="station"]["subway"="yes"]({bbox});',
        "center",
    ),
    Layer(
        "poi_bus_stop",
        'node["highway"="bus_stop"]({bbox});'
        'node["public_transport"="platform"]["bus"="yes"]({bbox});',
        "center",
    ),
    Layer("poi_hospital", 'nwr["amenity"~"^(hospital|clinic)$"]({bbox});', "center"),
    Layer("poi_police", 'nwr["amenity"="police"]({bbox});', "center"),
    Layer("street_lamp", 'node["highway"="street_lamp"]({bbox});', "center"),
    Layer("surveillance", 'node["man_made"="surveillance"]({bbox});', "center"),
    # Ways that carry a `lit` tag either way. The ratio of lit=yes length to
    # total tagged length is the most directly meaningful lighting signal OSM
    # offers for Delhi -- far better covered than street_lamp nodes.
    Layer("highway_lit", 'way["highway"]["lit"]({bbox});', "geom"),
    Layer(
        "footway",
        'way["highway"~"^(footway|pedestrian|path|steps|living_street)$"]({bbox});',
        "geom",
    ),
    # Commercial/civic activity, used as a proxy for daytime footfall. OSM
    # has no footfall data; shop and amenity density is the standard stand-in
    # and is explicitly labelled as a proxy everywhere it surfaces.
    Layer(
        "activity",
        'nwr["shop"]({bbox});'
        'nwr["amenity"~"^(restaurant|cafe|fast_food|bar|pub|marketplace|bank|atm|'
        'pharmacy|school|college|university|place_of_worship|cinema|fuel|library|'
        'community_centre|theatre)$"]({bbox});',
        "center",
    ),
)

POI_LAYER_FOR_CATEGORY = {
    "metro": "poi_metro",
    "bus_stop": "poi_bus_stop",
    "hospital": "poi_hospital",
    "police": "poi_police",
}


# ---------------------------------------------------------------------------
# Overpass client
# ---------------------------------------------------------------------------


class OverpassError(RuntimeError):
    """Raised when every mirror failed for a query."""


def _tiles() -> list[tuple[float, float, float, float]]:
    """Split the study bbox into (south, west, north, east) tiles."""
    lat_lo, lat_hi = DELHI_LAT_RANGE
    lon_lo, lon_hi = DELHI_LON_RANGE
    n = OVERPASS_TILES_PER_SIDE
    dlat = (lat_hi - lat_lo) / n
    dlon = (lon_hi - lon_lo) / n
    return [
        (lat_lo + r * dlat, lon_lo + c * dlon, lat_lo + (r + 1) * dlat, lon_lo + (c + 1) * dlon)
        for r in range(n)
        for c in range(n)
    ]


def _run_overpass(query: str, attempts: int = 3) -> dict:
    """
    POST a query, rotating over mirrors and backing off on failure.

    The public Overpass instances answer 429 (rate limited) and 504 (server
    busy) routinely under load -- both are transient, so they are retried on
    the next mirror rather than surfaced.
    """
    last_error: Exception | None = None
    for attempt in range(attempts):
        for endpoint in OVERPASS_ENDPOINTS:
            try:
                response = httpx.post(
                    endpoint,
                    content=query.encode("utf-8"),
                    headers={
                        "User-Agent": OVERPASS_USER_AGENT,
                        "Content-Type": "text/plain; charset=utf-8",
                    },
                    timeout=OVERPASS_TIMEOUT_SECONDS,
                )
                if response.status_code in (429, 504):
                    last_error = OverpassError(f"{endpoint} returned {response.status_code}")
                    log.debug("Overpass %s busy (%s)", endpoint, response.status_code)
                    continue
                response.raise_for_status()
                return response.json()
            except Exception as exc:  # noqa: BLE001 - every mirror failure is retryable
                last_error = exc
                log.debug("Overpass %s failed: %s", endpoint, exc)
        backoff = 5 * (attempt + 1)
        log.info("All Overpass mirrors busy; retrying in %ss", backoff)
        time.sleep(backoff)
    raise OverpassError(f"All Overpass mirrors failed: {last_error}")


def _cache_path(layer: Layer, tile_index: int):
    return OSM_CACHE_DIR / f"{layer.name}__tile{tile_index:02d}.json"


def fetch_layer_tile(layer: Layer, tile_index: int, bbox: tuple, refresh: bool = False) -> dict:
    """Fetch one layer over one tile, using the on-disk cache when present."""
    path = _cache_path(layer, tile_index)
    if path.exists() and not refresh:
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            log.warning("Corrupt OSM cache at %s; refetching", path)

    bbox_str = ",".join(f"{v:.5f}" for v in bbox)
    out_clause = "out center tags;" if layer.out == "center" else "out geom tags;"
    query = (
        f"[out:json][timeout:{int(OVERPASS_TIMEOUT_SECONDS)}];"
        f"({layer.body.format(bbox=bbox_str)});"
        f"{out_clause}"
    )
    payload = _run_overpass(query)
    path.write_text(json.dumps(payload))
    return payload


def fetch_all(refresh: bool = False, progress=log.info) -> dict:
    """
    Download (or load from cache) every layer over every tile.

    Returns {layer_name: [element, ...]} with elements already flattened
    across tiles and de-duplicated by OSM id.
    """
    tiles = _tiles()
    collected: dict[str, dict[tuple[str, int], dict]] = {layer.name: {} for layer in LAYERS}
    snapshot_dates: set[str] = set()

    for layer in LAYERS:
        for tile_index, bbox in enumerate(tiles):
            payload = fetch_layer_tile(layer, tile_index, bbox, refresh=refresh)
            timestamp = str(payload.get("osm3s", {}).get("timestamp_osm_base") or "")
            # Mirrors disagree on this field -- some return a full ISO
            # timestamp, at least one returns a bare counter like "34". Only
            # something that parses as a date is worth reporting as the
            # snapshot vintage.
            if _DATE_PATTERN.match(timestamp[:10]):
                snapshot_dates.add(timestamp[:10])
            for element in payload.get("elements", []):
                collected[layer.name][(element.get("type", "?"), element.get("id", 0))] = element
        progress(f"  {layer.name}: {len(collected[layer.name])} elements")

    result = {name: list(elements.values()) for name, elements in collected.items()}
    result["_snapshot"] = sorted(snapshot_dates)
    return result


# ---------------------------------------------------------------------------
# Aggregation into POI and per-cell feature tables
# ---------------------------------------------------------------------------


def _element_point(element: dict) -> tuple[float, float] | None:
    """(lat, lon) of a node, or of a way/relation's `center`."""
    if "lat" in element and "lon" in element:
        return float(element["lat"]), float(element["lon"])
    center = element.get("center")
    if center:
        return float(center["lat"]), float(center["lon"])
    geometry = element.get("geometry") or []
    if geometry:
        return float(geometry[0]["lat"]), float(geometry[0]["lon"])
    return None


def build_poi_table(osm: dict) -> pd.DataFrame:
    """Flatten the four POI layers into one table of real OSM locations."""
    rows = []
    for category in POI_CATEGORIES:
        for element in osm.get(POI_LAYER_FOR_CATEGORY[category], []):
            point = _element_point(element)
            if point is None:
                continue
            rows.append(
                {
                    "category": category,
                    "lat": point[0],
                    "lon": point[1],
                    "osm_type": element.get("type"),
                    "osm_id": element.get("id"),
                    "name": (element.get("tags") or {}).get("name", ""),
                }
            )
    table = pd.DataFrame(rows, columns=["category", "lat", "lon", "osm_type", "osm_id", "name"])
    if table.empty:
        raise ValueError("No POIs found in the OSM extract -- refusing to build an empty table.")
    return table


def _way_length_by_cell(elements: list[dict]) -> dict[tuple[int, int], float]:
    """
    Distribute each way's length (km) across the grid cells it crosses.

    Each consecutive vertex pair is attributed to the cell containing its
    midpoint, so a long way spanning several cells contributes to each of
    them proportionally rather than landing entirely in one.
    """
    totals: dict[tuple[int, int], float] = {}
    for element in elements:
        geometry = element.get("geometry") or []
        if len(geometry) < 2:
            continue
        coords = [(float(g["lat"]), float(g["lon"])) for g in geometry]
        for (lat1, lon1), (lat2, lon2) in zip(coords, coords[1:], strict=False):
            length = polyline_length_km([(lat1, lon1), (lat2, lon2)])
            if length <= 0:
                continue
            cell = grid_indices((lat1 + lat2) / 2, (lon1 + lon2) / 2)
            totals[cell] = totals.get(cell, 0.0) + length
    return totals


def _point_count_by_cell(elements: list[dict]) -> dict[tuple[int, int], int]:
    counts: dict[tuple[int, int], int] = {}
    for element in elements:
        point = _element_point(element)
        if point is None:
            continue
        cell = grid_indices(*point)
        counts[cell] = counts.get(cell, 0) + 1
    return counts


def _normalise_density(values: np.ndarray, upper_percentile: float = 90.0) -> np.ndarray:
    """
    Map a non-negative density to [0, 1] with a log transform.

    Urban density measures are heavy tailed -- a handful of market blocks
    carry an order of magnitude more shops than everywhere else. Scaling
    linearly by the maximum would squash the entire rest of the city into
    the bottom few percent, so values are log1p-compressed and referenced
    against a high percentile rather than the outright maximum.

    The reference is taken over the **non-zero** values only. Most of the
    study bounding box is either outside Delhi or simply unmapped, and OSM
    records surveillance cameras and streetlights in only a small minority
    of cells even inside it. Including all those zeros dragged the 95th
    percentile itself to zero, which collapsed `cctv_coverage` and
    `streetlight_density` to a constant 0.0 across the entire city. Scaling
    against the places where the feature actually exists keeps genuine zeros
    at zero while giving everywhere else a meaningful spread.
    """
    compressed = np.log1p(np.maximum(np.asarray(values, dtype=float), 0.0))
    observed = compressed[compressed > 0]
    if observed.size == 0:
        return np.zeros_like(compressed)
    reference = float(np.percentile(observed, upper_percentile))
    if reference <= 0:
        return np.zeros_like(compressed)
    return np.clip(compressed / reference, 0.0, 1.0)


def _shrink_to_prior(
    values: np.ndarray, weights: np.ndarray, prior: float, prior_weight: float
) -> np.ndarray:
    """
    Shrink per-cell estimates toward a city-wide prior in proportion to how
    little evidence each cell actually has (James-Stein style).

    A cell with two metres of `lit`-tagged road should not be treated as
    confidently lit or unlit; a cell with ten kilometres of it should be
    taken close to at face value.
    """
    return (values * weights + prior * prior_weight) / (weights + prior_weight)


def build_grid_features(osm: dict) -> pd.DataFrame:
    """
    Aggregate the OSM extract into one row per grid cell.

    Returns the measured per-cell attributes the model consumes, plus the
    raw counts behind them and an `osm_coverage` score summarising how much
    evidence the cell actually has.
    """
    area_km2 = cell_area_km2()
    n = GRID_CELLS_PER_SIDE

    lamp_counts = _point_count_by_cell(osm.get("street_lamp", []))
    cctv_counts = _point_count_by_cell(osm.get("surveillance", []))
    activity_counts = _point_count_by_cell(osm.get("activity", []))
    footway_km = _way_length_by_cell(osm.get("footway", []))

    lit_ways = osm.get("highway_lit", [])
    lit_yes_km = _way_length_by_cell(
        [w for w in lit_ways if (w.get("tags") or {}).get("lit") == "yes"]
    )
    lit_tagged_km = _way_length_by_cell(lit_ways)

    rows = []
    for row_idx in range(n):
        for col_idx in range(n):
            cell = (row_idx, col_idx)
            rows.append(
                {
                    "area_code": area_code_from_indices(row_idx, col_idx),
                    "grid_row": row_idx,
                    "grid_col": col_idx,
                    "street_lamp_count": lamp_counts.get(cell, 0),
                    "cctv_count": cctv_counts.get(cell, 0),
                    "activity_count": activity_counts.get(cell, 0),
                    "footway_km": round(footway_km.get(cell, 0.0), 4),
                    "lit_yes_km": round(lit_yes_km.get(cell, 0.0), 4),
                    "lit_tagged_km": round(lit_tagged_km.get(cell, 0.0), 4),
                }
            )

    grid = pd.DataFrame(rows)

    # --- Densities (per km^2 / per km) -> [0, 1] scores ---------------------
    grid["streetlight_density"] = _normalise_density(
        grid["street_lamp_count"].to_numpy() / area_km2
    )
    grid["cctv_coverage"] = _normalise_density(grid["cctv_count"].to_numpy() / area_km2)
    grid["activity_density"] = _normalise_density(grid["activity_count"].to_numpy() / area_km2)
    grid["footpath_quality"] = _normalise_density(grid["footway_km"].to_numpy() / area_km2)

    # --- Lighting: share of `lit`-tagged road length tagged lit=yes ---------
    # Only ways that carry the tag at all inform this, so cells with little
    # tagged length are shrunk toward the city-wide share.
    tagged = grid["lit_tagged_km"].to_numpy()
    lit_share_raw = np.divide(
        grid["lit_yes_km"].to_numpy(), tagged, out=np.zeros(len(grid)), where=tagged > 0
    )
    city_lit_share = (
        float(grid["lit_yes_km"].sum() / grid["lit_tagged_km"].sum())
        if grid["lit_tagged_km"].sum() > 0
        else 0.5
    )
    grid["lit_share_score"] = np.round(
        _shrink_to_prior(lit_share_raw, tagged, city_lit_share, prior_weight=1.0), 4
    )
    lit_share = grid["lit_share_score"].to_numpy()

    # Blend the tag-derived share with mapped lamp density, weighted by how
    # many lamps were actually observed -- see features.blend_lighting for
    # why a fixed ratio would systematically punish under-mapped areas.
    grid["lighting_score"] = np.round(
        [
            blend_lighting(share, density, count)
            for share, density, count in zip(
                lit_share,
                grid["streetlight_density"].to_numpy(),
                grid["street_lamp_count"].to_numpy(), strict=False,
            )
        ],
        4,
    )

    # Commercial/civic density stands in for footfall.
    grid["crowd_density"] = grid["activity_density"]

    # --- How much evidence does each cell actually have? -------------------
    evidence = (
        grid["street_lamp_count"]
        + grid["cctv_count"]
        + grid["activity_count"]
        + grid["footway_km"] * 5
        + grid["lit_tagged_km"] * 5
    ).to_numpy()
    grid["osm_coverage"] = _normalise_density(evidence, upper_percentile=85.0)
    grid["osm_evidence"] = np.round(evidence, 3)

    # Infrastructure quality judged only on components actually mapped in
    # each cell. Averaging unmapped components in as zeros scored the whole
    # city at ~0.09 -- a measure of survey effort, not of infrastructure.
    grid["infra_score"] = [
        infra_score_from_components(
            lamp,
            cctv,
            footpath,
            {
                "streetlight_density": lamps > 0,
                "cctv_coverage": cameras > 0,
                "footpath_quality": footways > 0,
            },
        )
        for lamp, cctv, footpath, lamps, cameras, footways in zip(
            grid["streetlight_density"],
            grid["cctv_coverage"],
            grid["footpath_quality"],
            grid["street_lamp_count"],
            grid["cctv_count"],
            grid["footway_km"],
            strict=True,
        )
    ]

    for column in (
        "infra_score",
        "streetlight_density",
        "cctv_coverage",
        "activity_density",
        "footpath_quality",
        "lit_share_score",
        "lighting_score",
        "crowd_density",
        "osm_coverage",
    ):
        grid[column] = grid[column].round(4)

    return grid


def build_point_table(osm: dict) -> pd.DataFrame:
    """
    Flatten the point layers used for radius-kernel density measurement.

    Kept separate from the POI table because these are *density* inputs
    (how much lighting/surveillance/activity surrounds a point) rather than
    *distance* inputs (how far the nearest refuge is).
    """
    rows = []
    for kind in ("street_lamp", "surveillance", "activity"):
        for element in osm.get(kind, []):
            point = _element_point(element)
            if point is not None:
                rows.append({"kind": kind, "lat": point[0], "lon": point[1]})
    table = pd.DataFrame(rows, columns=["kind", "lat", "lon"])
    if table.empty:
        raise ValueError("No density points found in the OSM extract.")
    return table
