"""
A synthetic stand-in for the OpenStreetMap extract.

Used in exactly two situations: the test suite (which must be hermetic and
fast, and must not hammer a public Overpass mirror), and a first run with no
network, so the system still starts and demonstrates itself instead of
failing outright.

It emits the *same three tables* as `fetch_osm_data.py` -- grid features,
POI table, density points -- so there is only ever one downstream code path.
Whichever source is in use is recorded as `feature_source` and surfaced by
`/health`, the model card and the frontend, so a synthetic run can never be
mistaken for a real one.
"""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from app.config import (
    DELHI_LAT_RANGE,
    DELHI_LON_RANGE,
    GRID_CELLS_PER_SIDE,
    POI_CATEGORIES,
    RANDOM_SEED,
)
from app.geo import area_code_from_indices, cell_center


def _stable_unit(*parts: str) -> float:
    """Deterministic pseudo-random float in [0, 1) from string parts."""
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def build_grid_features() -> pd.DataFrame:
    """
    One row per grid cell, with correlated infrastructure attributes.

    Neighbourhoods that invest in one infrastructure dimension tend to invest
    in others, so lighting, surveillance and footpaths all share a per-cell
    `base_quality` term rather than being drawn independently.
    """
    rows = []
    for row_idx in range(GRID_CELLS_PER_SIDE):
        for col_idx in range(GRID_CELLS_PER_SIDE):
            area_code = area_code_from_indices(row_idx, col_idx)
            base_quality = _stable_unit(area_code, "quality")

            def mix(tag: str, weight: float, code=area_code, base=base_quality) -> float:
                return float(np.clip(base * weight + _stable_unit(code, tag) * (1 - weight), 0, 1))

            streetlight = mix("lamp", 0.6)
            cctv = mix("cctv", 0.5)
            footpath = mix("footpath", 0.6)
            lit_share = mix("lit", 0.7)
            activity = float(np.clip(_stable_unit(area_code, "activity") * 0.8 + 0.1, 0, 1))
            rows.append(
                {
                    "area_code": area_code,
                    "grid_row": row_idx,
                    "grid_col": col_idx,
                    "street_lamp_count": int(streetlight * 60),
                    "cctv_count": int(cctv * 12),
                    "activity_count": int(activity * 40),
                    "footway_km": round(footpath * 8, 4),
                    "lit_yes_km": round(lit_share * 6, 4),
                    "lit_tagged_km": 6.0,
                    "streetlight_density": round(streetlight, 4),
                    "cctv_coverage": round(cctv, 4),
                    "activity_density": round(activity, 4),
                    "footpath_quality": round(footpath, 4),
                    "lit_share_score": round(lit_share, 4),
                    "lighting_score": round(
                        float(np.clip(0.65 * lit_share + 0.35 * streetlight, 0, 1)), 4
                    ),
                    "crowd_density": round(activity, 4),
                    "infra_score": round(
                    float(np.clip(0.35 * streetlight + 0.35 * cctv + 0.30 * footpath, 0, 1)), 4
                ),
                "osm_coverage": round(float(np.clip(0.3 + base_quality * 0.6, 0, 1)), 4),
                    "osm_evidence": round(50 + base_quality * 200, 3),
                }
            )
    return pd.DataFrame(rows)


def build_poi_table(seed: int = RANDOM_SEED) -> pd.DataFrame:
    """Plausible counts of each POI category, scattered over the study area."""
    rng = np.random.default_rng(seed)
    counts = {"metro": 250, "bus_stop": 2200, "hospital": 600, "police": 240}
    rows = []
    for category in POI_CATEGORIES:
        n = counts.get(category, 100)
        lats = rng.uniform(*DELHI_LAT_RANGE, n)
        lons = rng.uniform(*DELHI_LON_RANGE, n)
        rows.extend(
            {"category": category, "lat": lat, "lon": lon, "osm_type": "node",
             "osm_id": -1, "name": ""}
            for lat, lon in zip(lats, lons, strict=False)
        )
    return pd.DataFrame(rows)


def build_point_table(grid: pd.DataFrame, seed: int = RANDOM_SEED) -> pd.DataFrame:
    """
    Scatter density points inside each cell in proportion to that cell's
    attributes, so the radius kernels see the same spatial structure the real
    extract would produce.
    """
    rng = np.random.default_rng(seed)
    lat_span = (DELHI_LAT_RANGE[1] - DELHI_LAT_RANGE[0]) / GRID_CELLS_PER_SIDE
    lon_span = (DELHI_LON_RANGE[1] - DELHI_LON_RANGE[0]) / GRID_CELLS_PER_SIDE

    rows = []
    for record in grid.itertuples():
        centre_lat, centre_lon = cell_center(record.grid_row, record.grid_col)
        for kind, count in (
            ("street_lamp", record.street_lamp_count),
            ("surveillance", record.cctv_count),
            ("activity", record.activity_count),
        ):
            if count <= 0:
                continue
            rows.extend(
                {
                    "kind": kind,
                    "lat": centre_lat + (rng.random() - 0.5) * lat_span,
                    "lon": centre_lon + (rng.random() - 0.5) * lon_span,
                }
                for _ in range(int(count))
            )
    return pd.DataFrame(rows, columns=["kind", "lat", "lon"])


def write_synthetic_artifacts() -> dict:
    """Write the three tables plus a density-scaling reference to artifacts/."""
    import json

    from app.config import (
        GRID_FEATURES_PATH,
        OSM_POINTS_PATH,
        OSM_SCALING_PATH,
        POI_TABLE_PATH,
    )
    from app.poi_index import DENSITY_RADIUS_KM, PointDensityIndex

    grid = build_grid_features()
    poi_table = build_poi_table()
    points = build_point_table(grid)

    grid.to_csv(GRID_FEATURES_PATH, index=False)
    poi_table.to_csv(POI_TABLE_PATH, index=False)
    points.to_csv(OSM_POINTS_PATH, index=False)

    index = PointDensityIndex(points)
    centres = np.array([cell_center(r, c) for r, c in grid[["grid_row", "grid_col"]].to_numpy()])
    index.fit_scaling(centres[:, 0], centres[:, 1])
    OSM_SCALING_PATH.write_text(
        json.dumps(
            {
                "radius_km": DENSITY_RADIUS_KM,
                "snapshot": ["synthetic"],
                "scaling": index.scaling,
                "infra_prior": round(float(grid["infra_score"].median()), 4),
                "source": "synthetic",
            },
            indent=2,
        )
    )
    return {"grid": len(grid), "pois": len(poi_table), "points": len(points)}
