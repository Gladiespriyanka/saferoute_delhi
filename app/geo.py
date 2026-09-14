"""
Geometry and spatial-indexing helpers shared by the data pipeline, the
feature builder and the service layer.

Kept dependency-light (numpy only) and free of any app-level imports
besides config, so it can be unit tested in isolation.
"""
from __future__ import annotations

import math

import numpy as np

from app.config import DELHI_LAT_RANGE, DELHI_LON_RANGE, GRID_CELLS_PER_SIDE

EARTH_RADIUS_KM = 6371.0088


def haversine_km(
    lat1: float, lon1: float, lat2: np.ndarray | float, lon2: np.ndarray | float
) -> np.ndarray | float:
    """Great-circle distance in km from one point to one-or-many others."""
    lat2 = np.asarray(lat2, dtype=float)
    lon2 = np.asarray(lon2, dtype=float)
    phi1 = math.radians(lat1)
    phi2 = np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + math.cos(phi1) * np.cos(phi2) * np.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def polyline_length_km(coords: list[tuple[float, float]]) -> float:
    """Total length of a (lat, lon) polyline in km."""
    if len(coords) < 2:
        return 0.0
    lats = np.array([c[0] for c in coords], dtype=float)
    lons = np.array([c[1] for c in coords], dtype=float)
    # Equirectangular approximation is accurate to well under a metre at the
    # sub-kilometre segment lengths OSM ways are made of, and is ~10x faster
    # than a full haversine per vertex pair across millions of vertices.
    mean_lat = math.radians(float(lats.mean()))
    dx = np.radians(np.diff(lons)) * math.cos(mean_lat)
    dy = np.radians(np.diff(lats))
    return float(np.sum(np.hypot(dx, dy)) * EARTH_RADIUS_KM)


# ---------------------------------------------------------------------------
# Grid ("area") indexing
#
# The city bounding box is divided into GRID_CELLS_PER_SIDE^2 cells. Each
# cell is the unit at which OSM infrastructure is aggregated and at which
# crowdsourced audits accumulate. It stands in for an administrative ward:
# coarse enough that OSM density estimates are stable, fine enough
# (~1.2 x 1.4 km) that two ends of a walking route usually land in
# different cells.
# ---------------------------------------------------------------------------

_LAT_LO, _LAT_HI = DELHI_LAT_RANGE
_LON_LO, _LON_HI = DELHI_LON_RANGE
_LAT_SPAN = _LAT_HI - _LAT_LO
_LON_SPAN = _LON_HI - _LON_LO


def grid_indices(lat: float, lon: float) -> tuple[int, int]:
    """Clamped (row, col) grid indices for a point."""
    lat_c = min(max(lat, _LAT_LO), _LAT_HI)
    lon_c = min(max(lon, _LON_LO), _LON_HI)
    row = int((lat_c - _LAT_LO) / _LAT_SPAN * GRID_CELLS_PER_SIDE)
    col = int((lon_c - _LON_LO) / _LON_SPAN * GRID_CELLS_PER_SIDE)
    return min(row, GRID_CELLS_PER_SIDE - 1), min(col, GRID_CELLS_PER_SIDE - 1)


def area_code_for_point(lat: float, lon: float) -> str:
    """Stable `AREA-rr-cc` identifier for the grid cell containing a point."""
    row, col = grid_indices(lat, lon)
    return f"AREA-{row:02d}-{col:02d}"


def area_code_from_indices(row: int, col: int) -> str:
    return f"AREA-{row:02d}-{col:02d}"


def cell_center(row: int, col: int) -> tuple[float, float]:
    """Centre (lat, lon) of a grid cell."""
    lat = _LAT_LO + (row + 0.5) * _LAT_SPAN / GRID_CELLS_PER_SIDE
    lon = _LON_LO + (col + 0.5) * _LON_SPAN / GRID_CELLS_PER_SIDE
    return lat, lon


def cell_area_km2() -> float:
    """Approximate ground area of one grid cell, in km^2."""
    mean_lat = math.radians((_LAT_LO + _LAT_HI) / 2)
    height_km = _LAT_SPAN / GRID_CELLS_PER_SIDE * (math.pi / 180) * EARTH_RADIUS_KM
    width_km = (
        _LON_SPAN / GRID_CELLS_PER_SIDE * (math.pi / 180) * EARTH_RADIUS_KM * math.cos(mean_lat)
    )
    return height_km * width_km


def all_area_codes() -> list[str]:
    return [
        area_code_from_indices(r, c)
        for r in range(GRID_CELLS_PER_SIDE)
        for c in range(GRID_CELLS_PER_SIDE)
    ]


def in_delhi_bbox(lat: float, lon: float) -> bool:
    return _LAT_LO <= lat <= _LAT_HI and _LON_LO <= lon <= _LON_HI
