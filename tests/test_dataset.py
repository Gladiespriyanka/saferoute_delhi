"""Dataset construction: measurement, label sampling, and provenance."""
from __future__ import annotations

import numpy as np
import pytest

from app.config import MODERATE_UPPER_BOUND, RISK_LABELS, SAFE_UPPER_BOUND
from app.dataset import (
    bayes_optimal_accuracy,
    label_probabilities,
    measure_points,
    sample_labels,
)


def test_label_probabilities_form_a_distribution():
    probabilities = label_probabilities(np.linspace(0, 1, 50))
    assert probabilities.shape == (50, 3)
    assert np.allclose(probabilities.sum(axis=1), 1.0)
    assert (probabilities >= 0).all()


def test_label_probabilities_are_monotone_in_risk():
    """Higher composite risk must never make Unsafe less likely."""
    probabilities = label_probabilities(np.linspace(0, 1, 100))
    unsafe = probabilities[:, RISK_LABELS.index("Unsafe")]
    safe = probabilities[:, RISK_LABELS.index("Safe")]
    assert np.all(np.diff(unsafe) >= -1e-12)
    assert np.all(np.diff(safe) <= 1e-12)


def test_a_point_on_a_threshold_is_genuinely_ambiguous():
    """The ambiguity at a boundary is what makes calibrated confidence mean something."""
    at_boundary = label_probabilities(np.array([SAFE_UPPER_BOUND]))[0]
    assert at_boundary.max() < 0.75
    far_from_boundary = label_probabilities(np.array([0.02]))[0]
    assert far_from_boundary.max() > 0.95


def test_thresholds_decide_the_most_likely_class():
    safe, moderate, unsafe = label_probabilities(
        np.array([SAFE_UPPER_BOUND - 0.15,
                  (SAFE_UPPER_BOUND + MODERATE_UPPER_BOUND) / 2,
                  MODERATE_UPPER_BOUND + 0.15])
    )
    assert RISK_LABELS[safe.argmax()] == "Safe"
    assert RISK_LABELS[moderate.argmax()] == "Moderate"
    assert RISK_LABELS[unsafe.argmax()] == "Unsafe"


def test_bayes_optimal_accuracy_is_bounded_below_one():
    """Sampled labels are not perfectly predictable, and the ceiling says so."""
    ceiling = bayes_optimal_accuracy(label_probabilities(np.linspace(0.1, 0.9, 500)))
    assert 1 / 3 <= ceiling < 1.0


def test_sampled_labels_match_their_probabilities():
    rng = np.random.default_rng(0)
    probabilities = np.tile([0.7, 0.2, 0.1], (20_000, 1))
    labels = sample_labels(probabilities, rng)
    _, counts = np.unique(labels, return_counts=True)
    shares = dict(zip(*np.unique(labels, return_counts=True), strict=False))
    assert shares["Safe"] / len(labels) == pytest.approx(0.7, abs=0.02)
    assert counts.sum() == len(labels)


def test_measure_points_returns_bounded_features(city_data):
    lats = np.array([28.61, 28.55, 28.75])
    lons = np.array([77.21, 77.05, 77.30])
    measured = measure_points(city_data, lats, lons)

    assert len(measured) == 3
    for column in ("lighting_score", "crowd_density", "cctv_coverage",
                   "footpath_quality", "infra_score", "isolation_index", "osm_coverage"):
        assert measured[column].between(0, 1).all(), column
    for column in ("dist_metro_km", "dist_bus_km", "dist_hospital_km", "dist_police_km"):
        assert (measured[column] >= 0).all(), column


def test_measured_features_vary_within_a_single_grid_cell():
    """
    Reading densities off the grid would make every point in a cell identical.
    They are measured with a radius kernel around the exact coordinate instead.
    """
    from app.dataset import load_delhi_data
    from app.geo import area_code_for_point, cell_center

    data = load_delhi_data()
    lat, lon = cell_center(5, 5)
    lats = np.array([lat - 0.004, lat, lat + 0.004])
    lons = np.array([lon - 0.004, lon, lon + 0.004])
    assert len({area_code_for_point(a, b) for a, b in zip(lats, lons, strict=False)}) == 1

    measured = measure_points(data, lats, lons)
    assert measured["dist_bus_km"].nunique() == 3


def test_training_frame_is_well_formed(training_frame):
    assert len(training_frame) == 2500
    assert set(training_frame["label"]).issubset(set(RISK_LABELS))
    assert training_frame["composite_risk_score"].between(0, 1).all()
    probability_columns = [f"p_{label.lower()}" for label in RISK_LABELS]
    assert np.allclose(training_frame[probability_columns].sum(axis=1), 1.0)


def test_training_frame_covers_more_than_one_area(training_frame):
    """A spatially-blocked split needs many distinct cells to be meaningful."""
    assert training_frame["area_code"].nunique() > 20


def test_describe_reports_provenance(city_data):
    described = city_data.describe()
    assert described["feature_source"] == "synthetic"
    assert described["grid_cells"] > 0
    assert set(described["poi_counts"]) == {"metro", "bus_stop", "hospital", "police"}
