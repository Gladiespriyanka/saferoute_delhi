"""Geometry and grid-indexing invariants."""
from __future__ import annotations

import math

import numpy as np
import pytest

from app.config import DELHI_LAT_RANGE, DELHI_LON_RANGE, GRID_CELLS_PER_SIDE
from app.geo import (
    area_code_for_point,
    cell_area_km2,
    cell_center,
    grid_indices,
    haversine_km,
    in_delhi_bbox,
    polyline_length_km,
)


def test_haversine_known_distance():
    # India Gate (28.6129, 77.2295) to Qutub Minar (28.5245, 77.1855): about
    # 0.088 degrees of latitude and 0.044 of longitude apart, so ~10.7 km
    # straight line.
    distance = float(haversine_km(28.6129, 77.2295, 28.5245, 77.1855))
    assert 10.4 < distance < 11.0


def test_haversine_is_zero_for_identical_points():
    assert float(haversine_km(28.6, 77.2, 28.6, 77.2)) == pytest.approx(0.0, abs=1e-9)


def test_haversine_vectorises():
    distances = haversine_km(28.6, 77.2, np.array([28.6, 28.7]), np.array([77.2, 77.2]))
    assert distances.shape == (2,)
    assert distances[0] < distances[1]


def test_polyline_length_matches_haversine_for_two_points():
    coords = [(28.60, 77.20), (28.65, 77.25)]
    assert polyline_length_km(coords) == pytest.approx(
        float(haversine_km(*coords[0], *coords[1])), rel=0.01
    )


def test_polyline_length_of_degenerate_line_is_zero():
    assert polyline_length_km([(28.6, 77.2)]) == 0.0
    assert polyline_length_km([]) == 0.0


def test_grid_indices_cover_the_bbox_corners():
    lat_lo, lat_hi = DELHI_LAT_RANGE
    lon_lo, lon_hi = DELHI_LON_RANGE
    assert grid_indices(lat_lo, lon_lo) == (0, 0)
    last = GRID_CELLS_PER_SIDE - 1
    assert grid_indices(lat_hi, lon_hi) == (last, last)


def test_grid_indices_clamp_points_outside_the_bbox():
    """Routes run past the city edge; they must not produce an invalid cell."""
    assert grid_indices(0.0, 0.0) == (0, 0)
    last = GRID_CELLS_PER_SIDE - 1
    assert grid_indices(89.0, 179.0) == (last, last)


def test_area_code_round_trips_through_its_own_cell_centre():
    for row in (0, GRID_CELLS_PER_SIDE // 2, GRID_CELLS_PER_SIDE - 1):
        for col in (0, GRID_CELLS_PER_SIDE - 1):
            lat, lon = cell_center(row, col)
            assert area_code_for_point(lat, lon) == f"AREA-{row:02d}-{col:02d}"


def test_cell_area_is_plausible():
    area = cell_area_km2()
    span_km = 0.5 * math.pi / 180 * 6371
    assert 0 < area < span_km**2


def test_in_delhi_bbox():
    assert in_delhi_bbox(28.6139, 77.2090)
    assert not in_delhi_bbox(19.07, 72.87)  # Mumbai
