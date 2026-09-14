"""
Builds the labelled training dataset from the real OpenStreetMap extract.

Two design choices here are what make the model's *confidence* mean
something, and they are worth spelling out because the previous version of
this project got both wrong.

**1. Labels are sampled, not thresholded.**
The transparent composite-risk formula (app/config.COMPOSITE_WEIGHTS) gives
a continuous risk score. Turning that into a hard Safe/Moderate/Unsafe label
by thresholding makes the target a deterministic function of the very
features the model is given -- so the model can drive its training error to
essentially zero, reports ~99% confidence on everything, and that confidence
means nothing. Instead the composite is pushed through an **ordered logit**
to get genuine class *probabilities*, and each row's label is sampled from
them. Rows near a threshold are genuinely ambiguous, exactly as real safety
outcomes are. The model's job becomes recovering those probabilities, so a
calibrated 0.7 really does mean "about 70% of places like this are Unsafe",
and the achievable accuracy has an honest ceiling we can measure (see
`bayes_optimal_accuracy`).

**2. The train/test split is spatially blocked.**
Two samples drawn from the same ~1.2 x 1.4 km cell share their lighting,
surveillance and footpath measurements. Splitting rows at random puts near
duplicates on both sides of the split and inflates test accuracy by a wide
margin. Whole cells are held out instead, so the test score answers the
question that matters: how well does this generalise to a part of the city
it has never seen?

The composite formula remains a bootstrap. Replacing it with real incident
and audit outcomes is the intended next step; `app/service.py` already folds
crowdsourced audits back in between retrains.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.config import (
    DELHI_LAT_RANGE,
    DELHI_LON_RANGE,
    GRID_CELLS_PER_SIDE,
    GRID_FEATURES_PATH,
    LABEL_SAMPLING_TEMPERATURE,
    MODERATE_UPPER_BOUND,
    OSM_POINTS_PATH,
    OSM_SCALING_PATH,
    POI_TABLE_PATH,
    RANDOM_SEED,
    RISK_LABELS,
    SAFE_UPPER_BOUND,
)
from app.crime_data import DistrictCrime, attach_crime_to_grid, load_district_crime
from app.features import (
    DEFAULT_INFRA_PRIOR,
    blend_lighting,
    composite_weights,
    cyclic_time_features,
    infra_score_from_components,
    isolation_index,
    time_of_day_risk,
)
from app.geo import area_code_for_point
from app.poi_index import PoiIndex, PointDensityIndex

log = logging.getLogger(__name__)

_CELL_HEIGHT = (DELHI_LAT_RANGE[1] - DELHI_LAT_RANGE[0]) / GRID_CELLS_PER_SIDE
_CELL_WIDTH = (DELHI_LON_RANGE[1] - DELHI_LON_RANGE[0]) / GRID_CELLS_PER_SIDE


class MissingOsmDataError(RuntimeError):
    """Raised when the OpenStreetMap extract has not been downloaded yet."""


@dataclass
class DelhiData:
    """Everything the pipeline knows about the city, loaded once."""

    grid: pd.DataFrame
    poi_index: PoiIndex
    density_index: PointDensityIndex
    crime: DistrictCrime
    source: str
    snapshot: list[str] = field(default_factory=list)
    # Infrastructure quality assumed where nothing was mapped at all.
    infra_prior: float = DEFAULT_INFRA_PRIOR

    @property
    def with_crime(self) -> bool:
        return self.crime.available and "crime_risk_index" in self.grid.columns

    def describe(self) -> dict:
        return {
            "feature_source": self.source,
            "osm_snapshot": ", ".join(self.snapshot) if self.snapshot else "unknown",
            "poi_counts": self.poi_index.counts,
            "grid_cells": int(len(self.grid)),
            "mapped_cells": int((self.grid["osm_evidence"] > 0).sum()),
            "crime_data": self.crime.description,
        }


def load_delhi_data(allow_synthetic: bool = True) -> DelhiData:
    """
    Load the real OSM-derived city tables.

    When the extract is missing, falls back to a clearly-labelled synthetic
    city so the system still starts (see app/synthetic.py). Pass
    `allow_synthetic=False` to raise MissingOsmDataError instead -- which is
    what the training entry point does, since silently training on synthetic
    data would defeat the purpose.
    """
    missing = [
        p.name for p in (GRID_FEATURES_PATH, POI_TABLE_PATH, OSM_POINTS_PATH) if not p.exists()
    ]
    if missing:
        if not allow_synthetic:
            raise MissingOsmDataError(
                f"Missing OpenStreetMap artifact(s): {', '.join(missing)}. "
                "Run `python fetch_osm_data.py` first."
            )
        from app.synthetic import write_synthetic_artifacts

        log.warning(
            "No OpenStreetMap extract found (%s). Falling back to SYNTHETIC city data so the "
            "system can still start -- scores from it are illustrative only. Run "
            "`python fetch_osm_data.py` for real measurements.",
            ", ".join(missing),
        )
        write_synthetic_artifacts()

    grid = pd.read_csv(GRID_FEATURES_PATH)
    poi_table = pd.read_csv(POI_TABLE_PATH)
    points = pd.read_csv(OSM_POINTS_PATH)

    scaling, snapshot, source = {}, [], "openstreetmap"
    infra_prior = DEFAULT_INFRA_PRIOR
    if OSM_SCALING_PATH.exists():
        payload = json.loads(OSM_SCALING_PATH.read_text())
        scaling = payload.get("scaling", {})
        snapshot = payload.get("snapshot", [])
        source = payload.get("source", "openstreetmap")
        infra_prior = float(payload.get("infra_prior", DEFAULT_INFRA_PRIOR))

    crime = load_district_crime()
    grid = attach_crime_to_grid(grid, crime)

    return DelhiData(
        grid=grid,
        poi_index=PoiIndex(poi_table),
        density_index=PointDensityIndex(points, scaling),
        crime=crime,
        source=source,
        snapshot=snapshot,
        infra_prior=infra_prior,
    )


# ---------------------------------------------------------------------------
# Point -> feature measurement
# ---------------------------------------------------------------------------


def measure_points(data: DelhiData, lats: np.ndarray, lons: np.ndarray) -> pd.DataFrame:
    """
    Measure the place-dependent (time-independent) features for many points.

    Density features come from radius kernels around each exact coordinate;
    the lit-tag share and footpath density are cell-level because they are
    derived from way *lengths*, which only make sense aggregated over an area.
    """
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)

    densities = data.density_index.densities_batch(lats, lons)
    distances = data.poi_index.nearest_distances_batch(lats, lons)

    area_codes = [area_code_for_point(lat, lon) for lat, lon in zip(lats, lons, strict=False)]
    cell_lookup = data.grid.set_index("area_code")
    columns = [
        "lit_share_score",
        "footpath_quality",
        "osm_coverage",
        "lit_tagged_km",
        "footway_km",
    ]
    if "crime_risk_index" in data.grid.columns:
        columns.append("crime_risk_index")
    cells = cell_lookup.reindex(area_codes)[columns].reset_index(drop=True)

    streetlight = densities["street_lamp"]
    cctv = densities["surveillance"]
    activity = densities["activity"]

    measured = pd.DataFrame(
        {
            "lat": lats,
            "lon": lons,
            "area_code": area_codes,
            "streetlight_density": streetlight,
            "cctv_coverage": cctv,
            "crowd_density": activity,
            "footpath_quality": cells["footpath_quality"].fillna(0.0).to_numpy(),
            # Same evidence-weighted blend fetch_osm_data.py applies per cell,
            # here against the radius-kernel lamp count around this exact point.
            "lighting_score": [
                blend_lighting(share, density, count)
                for share, density, count in zip(
                    cells["lit_share_score"].fillna(0.5).to_numpy(),
                    streetlight,
                    densities["_counts"]["street_lamp"], strict=False,
                )
            ],
            "osm_coverage": cells["osm_coverage"].fillna(0.0).to_numpy(),
            # Whether lighting was measured here at all, or imputed from the
            # city-wide prior. OpenStreetMap records a `lit` tag for only
            # about a tenth of mapped Delhi and a streetlight for about a
            # twenty-fifth, so most places get the prior -- and anything that
            # reports a lighting figure to a user needs to know which it had.
            "lighting_observed": (
                (cells["lit_tagged_km"].fillna(0.0).to_numpy() > 0)
                | (np.asarray(densities["_counts"]["street_lamp"]) > 0)
            ),
            # Same question per infrastructure component: was anything mapped
            # here at all? A zero that means "nobody surveyed this" must not
            # be scored as "this place has none".
            "streetlight_observed": np.asarray(densities["_counts"]["street_lamp"]) > 0,
            "cctv_observed": np.asarray(densities["_counts"]["surveillance"]) > 0,
            "footpath_observed": cells["footway_km"].fillna(0.0).to_numpy() > 0,
            "dist_metro_km": distances["metro"],
            "dist_bus_km": distances["bus_stop"],
            "dist_hospital_km": distances["hospital"],
            "dist_police_km": distances["police"],
        }
    )
    if "crime_risk_index" in cells.columns:
        median = float(cells["crime_risk_index"].median(skipna=True))
        measured["crime_risk_index"] = cells["crime_risk_index"].fillna(median).to_numpy()

    measured["infra_score"] = [
        infra_score_from_components(
            lamp,
            cctv,
            footpath,
            {
                "streetlight_density": bool(lamp_seen),
                "cctv_coverage": bool(cctv_seen),
                "footpath_quality": bool(footpath_seen),
            },
            data.infra_prior,
        )
        for lamp, cctv, footpath, lamp_seen, cctv_seen, footpath_seen in zip(
            measured["streetlight_density"],
            measured["cctv_coverage"],
            measured["footpath_quality"],
            measured["streetlight_observed"],
            measured["cctv_observed"],
            measured["footpath_observed"],
            strict=True,
        )
    ]
    measured["isolation_index"] = [
        isolation_index(
            crowd,
            {"metro": m, "bus_stop": b, "hospital": h, "police": p},
        )
        for crowd, m, b, h, p in zip(
            measured["crowd_density"],
            measured["dist_metro_km"],
            measured["dist_bus_km"],
            measured["dist_hospital_km"],
            measured["dist_police_km"], strict=False,
        )
    ]
    return measured


# ---------------------------------------------------------------------------
# Label generation
# ---------------------------------------------------------------------------


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def label_probabilities(
    composite: np.ndarray, temperature: float = LABEL_SAMPLING_TEMPERATURE
) -> np.ndarray:
    """
    Ordered-logit class probabilities for a composite risk score.

    P(Safe)     = sigma((t1 - c) / T)
    P(Moderate) = sigma((t2 - c) / T) - sigma((t1 - c) / T)
    P(Unsafe)   = 1 - sigma((t2 - c) / T)

    T controls how wide the ambiguous band around each threshold is. A place
    sitting exactly on a threshold is a genuine coin flip; one far from both
    is near certain. Returns an (n, 3) array in RISK_LABELS order.
    """
    composite = np.asarray(composite, dtype=float)
    below_safe = _sigmoid((SAFE_UPPER_BOUND - composite) / temperature)
    below_moderate = _sigmoid((MODERATE_UPPER_BOUND - composite) / temperature)
    return np.column_stack(
        [below_safe, np.maximum(below_moderate - below_safe, 0.0), 1.0 - below_moderate]
    )


def bayes_optimal_accuracy(probabilities: np.ndarray) -> float:
    """
    The best accuracy any classifier could achieve on labels drawn from these
    probabilities -- the ceiling our test accuracy should be compared against.
    """
    return float(np.mean(probabilities.max(axis=1)))


def sample_labels(probabilities: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Draw one label per row from its class probabilities."""
    cumulative = probabilities.cumsum(axis=1)
    draws = rng.random((len(probabilities), 1))
    indices = (draws > cumulative).sum(axis=1).clip(0, len(RISK_LABELS) - 1)
    return np.array(RISK_LABELS, dtype=object)[indices]


# ---------------------------------------------------------------------------
# Dataset assembly
# ---------------------------------------------------------------------------


def build_training_dataset(
    data: DelhiData, n_samples: int = 20_000, seed: int = RANDOM_SEED
) -> pd.DataFrame:
    """
    Sample points across the mapped parts of Delhi at random times, measure
    their real features, and attach bootstrapped probabilistic labels.
    """
    rng = np.random.default_rng(seed)

    mapped = data.grid[data.grid["osm_evidence"] > 0]
    if mapped.empty:
        raise ValueError("No grid cell has any OSM evidence -- is the extract empty?")
    log.info(
        "Sampling %d points across %d mapped cells (of %d).",
        n_samples,
        len(mapped),
        len(data.grid),
    )

    # Sample cells uniformly, then jitter within each so that POI distances
    # and density kernels vary continuously instead of one value per cell.
    chosen = mapped.iloc[rng.integers(0, len(mapped), n_samples)]
    lats = (
        DELHI_LAT_RANGE[0]
        + (chosen["grid_row"].to_numpy() + rng.random(n_samples)) * _CELL_HEIGHT
    )
    lons = (
        DELHI_LON_RANGE[0]
        + (chosen["grid_col"].to_numpy() + rng.random(n_samples)) * _CELL_WIDTH
    )

    frame = measure_points(data, lats, lons)
    frame["hour"] = rng.integers(0, 24, n_samples)
    frame["day_of_week"] = rng.integers(0, 7, n_samples)

    time_features = pd.DataFrame(
        [
            cyclic_time_features(int(hour), int(day))
            for hour, day in zip(frame["hour"], frame["day_of_week"], strict=True)
        ]
    )
    frame = pd.concat([frame, time_features], axis=1)
    frame["time_of_day_risk"] = [time_of_day_risk(int(h)) for h in frame["hour"]]

    frame["infra_risk"] = 1.0 - frame["infra_score"]
    frame["lighting_risk"] = 1.0 - frame["lighting_score"]

    weights = composite_weights(data.with_crime)
    composite = sum(weight * frame[name].to_numpy() for name, weight in weights.items())
    frame["composite_risk_score"] = np.clip(composite, 0.0, 1.0)

    probabilities = label_probabilities(frame["composite_risk_score"].to_numpy())
    for index, name in enumerate(RISK_LABELS):
        frame[f"p_{name.lower()}"] = probabilities[:, index]
    frame["label"] = sample_labels(probabilities, rng)

    log.info(
        "Label mix: %s | Bayes-optimal accuracy ceiling: %.4f",
        frame["label"].value_counts(normalize=True).round(3).to_dict(),
        bayes_optimal_accuracy(probabilities),
    )
    return frame
