"""
Nearest-POI lookup.

The previous implementation re-filtered the whole POI DataFrame by category
and recomputed a haversine distance against every row, once per category,
per segment, on every single request -- O(categories x |POI|) pandas work in
the hot path. With the real OpenStreetMap extract (thousands of bus stops
alone) that is the dominant cost of scoring a route.

Here the POI table is turned into one BallTree per category at load time,
which answers a nearest-neighbour query in O(log n) and handles a whole
route's segments in a single vectorised call.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

from app.config import POI_CATEGORIES
from app.geo import EARTH_RADIUS_KM

# Distance reported for a category with no POIs at all. Far enough to read
# as "no help nearby" without being an outlier the model has never seen.
NO_POI_DISTANCE_KM = 5.0


class PoiIndex:
    """Category-partitioned nearest-neighbour index over the POI table."""

    def __init__(self, poi_table: pd.DataFrame):
        self._trees: dict[str, BallTree] = {}
        self._counts: dict[str, int] = {}
        for category in POI_CATEGORIES:
            subset = poi_table[poi_table["category"] == category]
            self._counts[category] = len(subset)
            if subset.empty:
                continue
            # BallTree's haversine metric works in radians and returns
            # angular distance, which scales to km by the Earth's radius.
            coords = np.radians(subset[["lat", "lon"]].to_numpy(dtype=float))
            self._trees[category] = BallTree(coords, metric="haversine")

    @property
    def counts(self) -> dict[str, int]:
        """Number of indexed POIs per category."""
        return dict(self._counts)

    def nearest_distances(self, lat: float, lon: float) -> dict[str, float]:
        """Nearest distance in km to each POI category for a single point."""
        return {
            category: float(distances[0])
            for category, distances in self.nearest_distances_batch(
                np.array([lat]), np.array([lon])
            ).items()
        }

    def nearest_distances_batch(
        self, lats: np.ndarray, lons: np.ndarray
    ) -> dict[str, np.ndarray]:
        """Nearest distance in km to each POI category for many points at once."""
        query = np.radians(np.column_stack([np.asarray(lats, dtype=float),
                                            np.asarray(lons, dtype=float)]))
        result: dict[str, np.ndarray] = {}
        for category in POI_CATEGORIES:
            tree = self._trees.get(category)
            if tree is None:
                result[category] = np.full(len(query), NO_POI_DISTANCE_KM)
                continue
            angular, _ = tree.query(query, k=1)
            result[category] = angular[:, 0] * EARTH_RADIUS_KM
        return result


def great_circle_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Scalar haversine distance, for one-off comparisons."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, a)))


# ---------------------------------------------------------------------------
# Point-level density kernels
# ---------------------------------------------------------------------------

# Radius over which streetlight / CCTV / activity density is measured around
# a point. ~5 minutes' walk: wide enough that a handful of missing OSM nodes
# doesn't swing the estimate, tight enough to distinguish one street from the
# next.
DENSITY_RADIUS_KM = 0.4
DENSITY_KINDS = ("street_lamp", "surveillance", "activity")


class PointDensityIndex:
    """
    Measures how dense mapped streetlights / cameras / commercial activity are
    around an arbitrary point.

    Reading these off the ~1.2 x 1.4 km analysis grid instead would make every
    point in a cell identical and introduce a hard discontinuity at each cell
    edge -- two ends of the same street could score very differently purely
    because a grid line ran between them. A radius kernel around the actual
    coordinate varies continuously and reflects what is really nearby.

    `scaling` holds the log-density reference value per kind, computed once
    over the whole city at fetch time and persisted, so a score means the
    same thing at inference as it did during training.
    """

    def __init__(self, points: pd.DataFrame, scaling: dict[str, float] | None = None):
        self._trees: dict[str, BallTree] = {}
        for kind in DENSITY_KINDS:
            subset = points[points["kind"] == kind]
            if subset.empty:
                continue
            coords = np.radians(subset[["lat", "lon"]].to_numpy(dtype=float))
            self._trees[kind] = BallTree(coords, metric="haversine")
        self.scaling = dict(scaling or {})
        self._radius_rad = DENSITY_RADIUS_KM / EARTH_RADIUS_KM
        self._area_km2 = math.pi * DENSITY_RADIUS_KM**2

    def raw_counts_batch(self, lats: np.ndarray, lons: np.ndarray) -> dict[str, np.ndarray]:
        """Number of mapped features of each kind within DENSITY_RADIUS_KM."""
        query = np.radians(np.column_stack([np.asarray(lats, dtype=float),
                                            np.asarray(lons, dtype=float)]))
        counts: dict[str, np.ndarray] = {}
        for kind in DENSITY_KINDS:
            tree = self._trees.get(kind)
            if tree is None:
                counts[kind] = np.zeros(len(query))
                continue
            counts[kind] = tree.query_radius(query, r=self._radius_rad, count_only=True).astype(
                float
            )
        return counts

    def fit_scaling(self, lats: np.ndarray, lons: np.ndarray, percentile: float = 90.0) -> None:
        """
        Derive the per-kind log-density reference from a sample of the city.

        The percentile is taken over points that observed *something*. Most
        of the study area is unmapped or outside Delhi, and streetlights and
        cameras are recorded in only a small minority of places even inside
        it -- so including every zero puts the percentile itself at zero and
        collapses the whole feature to a constant.
        """
        counts = self.raw_counts_batch(lats, lons)
        for kind, values in counts.items():
            compressed = np.log1p(np.asarray(values, dtype=float) / self._area_km2)
            observed = compressed[compressed > 0]
            reference = float(np.percentile(observed, percentile)) if observed.size else 0.0
            self.scaling[kind] = reference if reference > 0 else 1.0

    def densities_batch(self, lats: np.ndarray, lons: np.ndarray) -> dict[str, np.ndarray]:
        """Density of each kind around each point, scaled to [0, 1]."""
        counts = self.raw_counts_batch(lats, lons)
        scores: dict[str, np.ndarray] = {}
        for kind, values in counts.items():
            reference = self.scaling.get(kind) or 1.0
            scores[kind] = np.clip(np.log1p(values / self._area_km2) / reference, 0.0, 1.0)
        scores["_counts"] = counts
        return scores

    def densities(self, lat: float, lon: float) -> dict[str, float]:
        """Density of each kind around one point, scaled to [0, 1]."""
        batch = self.densities_batch(np.array([lat]), np.array([lon]))
        result = {kind: float(batch[kind][0]) for kind in DENSITY_KINDS}
        result["_evidence"] = float(sum(batch["_counts"][k][0] for k in DENSITY_KINDS))
        return result
