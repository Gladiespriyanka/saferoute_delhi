# SafeHerWay safety model - model card

Generated 2026-09-13T17:57:32+00:00 - model version 0.2.0

## What it does

Classifies a point on a walking route as **Safe / Moderate / Unsafe** at a given hour and day of week, and reports a calibrated probability for each class. The route-level score is an aggregate of its segments (weighted toward the worst one), which live weather and traffic context may then nudge within a hard cap.

## Data

| Source | Value |
| --- | --- |
| `feature_source` | openstreetmap |
| `osm_snapshot` | 2026-09-13 |
| `poi_counts` | {'metro': 172, 'bus_stop': 1331, 'hospital': 732, 'police': 125} |
| `grid_cells` | 1600 |
| `mapped_cells` | 592 |
| `crime_data` | not configured (see data/README.md) |

Environmental features are **measured from OpenStreetMap**: streetlight and surveillance node density around each point, `lit=yes` tagged road share, footway length density, commercial activity density as a footfall proxy, and true distances to the nearest metro station, bus stop, hospital and police station.

> **Crime data is not configured.** `crime_risk_index` is excluded from the feature set entirely and its composite weight redistributed over the measured features. This model has therefore been trained *only* on measured OpenStreetMap quantities - no crime figures were invented to fill the gap. See `data/README.md` to populate it.

## Labels are bootstrapped, and that is the main limitation

No ground-truth incident labels were available, so labels come from a transparent weighted composite of the measured features, pushed through an ordered logit and **sampled** rather than thresholded. That deliberately builds in irreducible uncertainty: places near a class boundary are genuine coin flips, which is what makes a calibrated confidence number meaningful instead of a uniform 0.99.

The consequence to keep in mind: **the model has learned a smoothed version of that formula as applied to real geography, not real safety outcomes.** It is a well-engineered prior over Delhi's walking infrastructure, not an empirical crime predictor. Replacing the composite with real incident and audit outcomes is the single highest-value next step.

## Evaluation

Rows are split by **whole grid cell**, never at random. Samples from the same ~1.2 x 1.4 km cell share their infrastructure measurements, so a random row split would scatter near-duplicates across train and test and inflate the score. The four-way fit / calibrate / select / test split means the calibration method was chosen on data the calibrators never saw, and these test numbers were produced exactly once, on data nothing was selected with.

| Metric | Model | Best achievable | Notes |
| --- | --- | --- | --- |
| Accuracy | 0.7778 | 0.8049 | higher is better |
| Macro F1 | 0.7800 | - | higher is better |
| Log loss | 0.5510 | 0.4829 | lower is better |
| Brier score | 0.3110 | 0.2928 | lower is better |
| Expected calibration error | 0.0236 | 0 | lower is better |
| Max calibration error | 0.0462 | 0 | lower is better |
| Mean confidence | 0.7880 | 0.7778 | should match accuracy |

Calibration method selected: **isotonic**.

The *best achievable* column is not 1.0 (or 0.0). Labels are sampled from class probabilities, so a share of them is irreducibly unpredictable; these are the values a model that knew the true probabilities exactly would score. Judging accuracy against 100% would understate the model and judging log loss against 0 would be meaningless.

**Calibration verdict:** well calibrated - the reported confidence can be taken at face value.

This model reaches **96.6% of the achievable accuracy ceiling** (0.778 of a possible 0.805).

### Calibration methods compared (on the selection split)

| Method | Log loss | ECE | Brier | Accuracy |
| --- | --- | --- | --- | --- |
| isotonic | 0.5020 | 0.0268 | 0.3073 | 0.7853 |
| sigmoid | 0.5164 | 0.0335 | 0.3098 | 0.7792 |
| uncalibrated | 0.5000 | 0.0221 | 0.3108 | 0.7733 |

### Per-class performance

| Class | Precision | Recall | F1 | Support |
| --- | --- | --- | --- | --- |
| Safe | 0.824 | 0.726 | 0.772 | 892 |
| Moderate | 0.710 | 0.782 | 0.745 | 1541 |
| Unsafe | 0.839 | 0.808 | 0.823 | 1316 |

### Confusion matrix (rows = actual, columns = predicted)

| | Safe | Moderate | Unsafe |
| --- | --- | --- | --- |
| **Safe** | 648 | 242 | 2 |
| **Moderate** | 134 | 1205 | 202 |
| **Unsafe** | 4 | 249 | 1063 |

### What the features actually contain

`observed` is the share of sampled points where the feature was really measured rather than imputed from a city-wide prior, and `std` is how much it varies across the city. A feature that is mostly imputed, or barely varies, cannot carry information the model does not have -- which an accuracy figure alone would hide completely.

| Feature | Mean | Std | Observed |
| --- | --- | --- | --- |
| `hour_sin` | 0.003 | 0.706 | 100% |
| `hour_cos` | -0.004 | 0.708 | 100% |
| `dow_sin` | 0.003 | 0.706 | 100% |
| `dow_cos` | -0.006 | 0.708 | 100% |
| `lighting_score` | 0.723 | 0.060 | 11% ⚠️ |
| `crowd_density` | 0.256 | 0.332 | 44% ⚠️ |
| `cctv_coverage` | 0.029 | 0.138 | 5% ⚠️ |
| `streetlight_density` | 0.009 | 0.075 | 2% ⚠️ |
| `footpath_quality` | 0.251 | 0.337 | 55% |
| `infra_score` | 0.423 | 0.248 | 100% |
| `isolation_index` | 0.655 | 0.237 | 100% |
| `time_of_day_risk` | 0.499 | 0.284 | 100% |
| `dist_metro_km` | 4.065 | 4.709 | 100% |
| `dist_bus_km` | 5.513 | 6.993 | 100% |
| `dist_hospital_km` | 2.598 | 2.404 | 100% |
| `dist_police_km` | 3.465 | 3.197 | 100% |

> Flagged above: `lighting_score`, `crowd_density`, `cctv_coverage`, `streetlight_density`. These are thin in OpenStreetMap for Delhi. They are kept in the feature set because a caller can override them per segment with a first-hand observation, and because they are informative where they do exist -- but the model is not learning much from them, and the explanations say when a value was imputed rather than measured.

### Permutation importance (test split, neg. log loss)

| Feature | Importance |
| --- | --- |
| `isolation_index` | +0.22340 |
| `time_of_day_risk` | +0.18593 |
| `infra_score` | +0.18371 |
| `hour_cos` | +0.10159 |
| `footpath_quality` | +0.05502 |
| `crowd_density` | +0.01816 |
| `dow_sin` | +0.01353 |
| `dist_police_km` | +0.00411 |
| `lighting_score` | +0.00249 |
| `dist_bus_km` | +0.00180 |
| `dist_hospital_km` | +0.00115 |
| `dow_cos` | +0.00062 |
| `streetlight_density` | +0.00041 |
| `dist_metro_km` | -0.00004 |
| `cctv_coverage` | -0.00152 |
| `hour_sin` | -0.00539 |

## Known limitations

- **OpenStreetMap coverage is uneven.** Central Delhi is mapped in far more detail than the outskirts, so a cell with no mapped streetlights is usually under-mapped rather than unlit. Sparse cells are shrunk toward the city mean and every prediction carries an `osm_coverage` score, but this remains the largest source of systematic error.
- **Commercial density is a proxy for footfall**, not a measurement of it. It will understate residential streets that are busy with people but have no shops.
- **The grid is not administrative wards.** ~1.2 x 1.4 km cells are a convenience, not a meaningful civic boundary.
- **Not a substitute for judgment.** The output is a prior derived from infrastructure data. It does not observe the street right now, and should never be the only input to a decision about personal safety.
