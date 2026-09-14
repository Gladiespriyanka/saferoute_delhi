"""
Download the real OpenStreetMap extract SafeHerWay's features are built
from, and aggregate it into the per-cell grid + POI tables.

    python fetch_osm_data.py              # uses artifacts/osm_cache/ if present
    python fetch_osm_data.py --refresh    # re-download every tile

Writes:
    artifacts/grid_features.csv   one row per ~1.2x1.4 km cell
    artifacts/poi_table.csv       every metro/bus/hospital/police point
    artifacts/osm_cache/*.json    raw Overpass responses (the audit trail)

This is an offline step. Nothing in the request path ever calls Overpass.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

import numpy as np

from app.config import (
    GRID_FEATURES_PATH,
    OSM_POINTS_PATH,
    OSM_SCALING_PATH,
    POI_TABLE_PATH,
)
from app.features import DEFAULT_INFRA_PRIOR
from app.geo import cell_center
from app.osm_data import (
    OverpassError,
    build_grid_features,
    build_poi_table,
    build_point_table,
    fetch_all,
)
from app.poi_index import DENSITY_RADIUS_KM, PointDensityIndex


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh", action="store_true", help="Ignore the cache and re-download every tile"
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    print("Fetching OpenStreetMap layers for Delhi via Overpass...")
    try:
        osm = fetch_all(refresh=args.refresh, progress=print)
    except OverpassError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        print(
            "The public Overpass mirrors are rate limited and frequently busy. "
            "Re-run this command in a few minutes -- tiles already downloaded are "
            "cached, so it resumes where it left off.",
            file=sys.stderr,
        )
        return 1

    snapshot = osm.get("_snapshot") or ["unknown"]
    print(f"\nOSM snapshot date(s): {', '.join(snapshot)}")

    poi_table = build_poi_table(osm)
    poi_table.to_csv(POI_TABLE_PATH, index=False)
    print(f"Wrote {len(poi_table)} POIs to {POI_TABLE_PATH}")
    print(poi_table["category"].value_counts().to_string())

    points = build_point_table(osm)
    points.to_csv(OSM_POINTS_PATH, index=False)
    print(f"\nWrote {len(points)} density points to {OSM_POINTS_PATH}")
    print(points["kind"].value_counts().to_string())

    grid = build_grid_features(osm)
    grid.to_csv(GRID_FEATURES_PATH, index=False)
    print(f"\nWrote {len(grid)} grid cells to {GRID_FEATURES_PATH}")

    # Fix the density scale once, over the whole city, so a "0.7 streetlight
    # density" means the same thing at inference time as it did in training.
    index = PointDensityIndex(points)
    centres = np.array([cell_center(r, c) for r, c in grid[["grid_row", "grid_col"]].to_numpy()])
    index.fit_scaling(centres[:, 0], centres[:, 1])
    # Infrastructure quality to assume where nothing at all was mapped:
    # the median across cells that did have something to measure, so the
    # fallback reflects this city rather than an arbitrary 0.5.
    observed_cells = grid[
        (grid["street_lamp_count"] > 0) | (grid["cctv_count"] > 0) | (grid["footway_km"] > 0)
    ]
    infra_prior = (
        round(float(observed_cells["infra_score"].median()), 4)
        if not observed_cells.empty
        else DEFAULT_INFRA_PRIOR
    )

    OSM_SCALING_PATH.write_text(
        json.dumps(
            {
                "radius_km": DENSITY_RADIUS_KM,
                "snapshot": snapshot,
                "scaling": index.scaling,
                "infra_prior": infra_prior,
            },
            indent=2,
        )
    )
    print(f"Infrastructure prior (median over {len(observed_cells)} mapped cells): {infra_prior}")
    print(f"Wrote density scaling reference to {OSM_SCALING_PATH}: "
          + ", ".join(f"{k}={v:.3f}" for k, v in index.scaling.items()))

    mapped = (grid["osm_evidence"] > 0).sum()
    print(f"Cells with any OSM evidence: {mapped}/{len(grid)} ({mapped / len(grid):.0%})")
    print("\nPer-cell feature summary:")
    print(
        grid[
            [
                "lighting_score",
                "streetlight_density",
                "cctv_coverage",
                "footpath_quality",
                "crowd_density",
                "osm_coverage",
            ]
        ]
        .describe()
        .round(3)
        .to_string()
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
