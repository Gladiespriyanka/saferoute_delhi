"""
The safety classifier: a calibrated RandomForest over the measured features.

Calibration is the whole point of this module. An uncalibrated tree ensemble
reports confidences that are not probabilities -- it will happily say 0.97
about cases it gets wrong a third of the time -- and this application puts
that number in front of someone deciding which street to walk down. So:

* the data is split **by grid cell**, never by row, so no near-duplicate of a
  training point can appear in the test set (see app/dataset.py);
* the split is four-way -- fit / calibrate / select / test -- so the
  calibration method is chosen on data the calibrators never saw, and the
  reported test metrics are produced exactly once, on data nothing was
  chosen with;
* both isotonic and sigmoid calibration are fitted and scored, and the model
  card records what each achieved rather than only the winner;
* the headline metrics are Expected Calibration Error, Brier score and log
  loss alongside accuracy, and accuracy is reported against the
  **Bayes-optimal ceiling** for the label noise -- "0.79 against a ceiling of
  0.82" is a far more useful statement than a bare "0.79".

The model artifact is a bundle, not a bare estimator: it carries its own
feature contract, class order, provenance and metrics, so a model can never
be silently fed the wrong columns after the feature set changes.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    f1_score,
    log_loss,
)

from app import __version__
from app.config import MODEL_METRICS_PATH, MODEL_PATH, RANDOM_SEED, RISK_LABELS
from app.dataset import bayes_optimal_accuracy

log = logging.getLogger(__name__)

# Scalar risk value each class maps to when collapsing a probability vector
# into a single score. Downstream aggregation and the contextual adjustments
# operate on this smooth score rather than a hard three-way split.
LABEL_SCALARS = {"Safe": 0.0, "Moderate": 0.5, "Unsafe": 1.0}

SPLIT_FRACTIONS = {"fit": 0.55, "calibrate": 0.15, "select": 0.15, "test": 0.15}


class FeatureContractError(ValueError):
    """Raised when input columns don't match what the model was trained on."""


@dataclass
class SafetyModel:
    """A fitted classifier bundled with everything needed to use it safely."""

    estimator: object
    feature_columns: list[str]
    classes: list[str]
    trained_at: str
    provenance: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    version: str = __version__

    def predict_proba(self, rows: pd.DataFrame | list[dict]) -> np.ndarray:
        """
        Class probabilities, with columns in `self.classes` order.

        scikit-learn orders `classes_` alphabetically -- Moderate, Safe,
        Unsafe -- while ours is semantic: Safe, Moderate, Unsafe. Reading the
        raw output positionally therefore swaps Safe and Moderate on every
        single prediction, which inverted the time-of-day relationship badly
        enough that 2am scored *safer* than midday. The columns are permuted
        back here, once, so no caller has to know.
        """
        frame = pd.DataFrame(rows) if not isinstance(rows, pd.DataFrame) else rows
        missing = [c for c in self.feature_columns if c not in frame.columns]
        if missing:
            raise FeatureContractError(
                f"Model expects feature(s) not present in the input: {', '.join(missing)}. "
                "The model artifact is stale -- re-run `python train_model.py`."
            )
        raw = self.estimator.predict_proba(frame[self.feature_columns])
        estimator_classes = list(self.estimator.classes_)
        if estimator_classes == self.classes:
            return raw
        return raw[:, [estimator_classes.index(c) for c in self.classes]]

    def score_rows(self, rows: list[dict]) -> list[dict]:
        """Label, calibrated confidence, margin and scalar risk for each row."""
        probabilities = self.predict_proba(rows)
        results = []
        for row in probabilities:
            order = np.argsort(row)[::-1]
            results.append(
                {
                    "label": self.classes[order[0]],
                    "confidence": float(row[order[0]]),
                    # How decisively the top class beat the runner-up. A 0.45
                    # top probability means something very different when the
                    # second class is at 0.44 than when it is at 0.10.
                    "margin": float(row[order[0]] - row[order[1]]),
                    "risk_score": float(
                        sum(p * LABEL_SCALARS[c] for p, c in zip(row, self.classes, strict=False))
                    ),
                    "class_probabilities": {
                        c: float(p) for c, p in zip(self.classes, row, strict=False)
                    },
                }
            )
        return results


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def expected_calibration_error(
    probabilities: np.ndarray, correct: np.ndarray, n_bins: int = 15
) -> tuple[float, float, list[dict]]:
    """
    Expected and maximum calibration error over equal-width confidence bins.

    ECE is the average gap between how confident the model was and how often
    it was actually right, weighted by how many predictions fell in each bin.
    0 is perfect; anything above ~0.05 means the reported confidence is
    materially misleading.
    """
    confidence = probabilities.max(axis=1)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    mce = 0.0
    bins = []
    for lower, upper in zip(edges[:-1], edges[1:], strict=False):
        in_bin = (confidence > lower) & (confidence <= upper)
        count = int(in_bin.sum())
        if count == 0:
            continue
        bin_confidence = float(confidence[in_bin].mean())
        bin_accuracy = float(correct[in_bin].mean())
        gap = abs(bin_confidence - bin_accuracy)
        ece += count / len(confidence) * gap
        mce = max(mce, gap)
        bins.append(
            {
                "range": [round(float(lower), 3), round(float(upper), 3)],
                "count": count,
                "mean_confidence": round(bin_confidence, 4),
                "accuracy": round(bin_accuracy, 4),
            }
        )
    return float(ece), float(mce), bins


def multiclass_brier(probabilities: np.ndarray, y_true: np.ndarray, classes: list[str]) -> float:
    """Mean squared error between predicted probabilities and the one-hot truth."""
    onehot = np.zeros_like(probabilities)
    index = {c: i for i, c in enumerate(classes)}
    for row, label in enumerate(y_true):
        onehot[row, index[label]] = 1.0
    return float(np.mean(np.sum((probabilities - onehot) ** 2, axis=1)))


def multiclass_log_loss(
    probabilities: np.ndarray, y_true: np.ndarray, classes: list[str]
) -> float:
    """
    Cross-entropy, with the column order handled explicitly.

    sklearn's `log_loss` assumes the columns of `y_prob` are in *sorted*
    label order regardless of the order passed as `labels`. Our class order
    is semantic (Safe, Moderate, Unsafe), which is not sorted, so passing the
    probabilities through unchanged silently scores them against permuted
    columns -- it reported a log loss of 2.1 for a model whose true value was
    0.5. The columns are reordered here instead.
    """
    order = sorted(range(len(classes)), key=lambda i: classes[i])
    return float(
        log_loss(y_true, probabilities[:, order], labels=[classes[i] for i in order])
    )


def evaluate(
    probabilities: np.ndarray,
    y_true: np.ndarray,
    classes: list[str],
    true_probabilities: np.ndarray | None = None,
) -> dict:
    """
    Full metric set for one set of predicted probabilities.

    When the label-generating probabilities are known (they are, because we
    generated them), the theoretically best achievable value of each metric
    is reported alongside it. "Log loss 0.52 against a floor of 0.49" is a
    statement someone can act on; a bare "0.52" is not.
    """
    predictions = np.array(classes)[probabilities.argmax(axis=1)]
    correct = (predictions == np.asarray(y_true)).astype(float)
    ece, mce, bins = expected_calibration_error(probabilities, correct)
    metrics = {
        "accuracy": float(correct.mean()),
        "macro_f1": float(f1_score(y_true, predictions, average="macro", zero_division=0)),
        "log_loss": multiclass_log_loss(probabilities, y_true, classes),
        "brier": multiclass_brier(probabilities, y_true, classes),
        "ece": ece,
        "mce": mce,
        "mean_confidence": float(probabilities.max(axis=1).mean()),
        "calibration_bins": bins,
    }
    if true_probabilities is not None:
        metrics["reference"] = {
            "accuracy": bayes_optimal_accuracy(true_probabilities),
            "log_loss": multiclass_log_loss(true_probabilities, y_true, classes),
            "brier": multiclass_brier(true_probabilities, y_true, classes),
        }
    return metrics


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


# Features whose value is a *measurement* that may be missing, mapped to
# the column that says whether it was actually observed. Anything not listed
# is always observed (distances, time encodings).
_OBSERVATION_TESTS = {
    "lighting_score": lambda df: df["lighting_observed"].to_numpy()
    if "lighting_observed" in df
    else None,
    "streetlight_density": lambda df: (df["streetlight_density"] > 0).to_numpy(),
    "cctv_coverage": lambda df: (df["cctv_coverage"] > 0).to_numpy(),
    "footpath_quality": lambda df: (df["footpath_quality"] > 0).to_numpy(),
    "crowd_density": lambda df: (df["crowd_density"] > 0).to_numpy(),
}


def feature_diagnostics(df: pd.DataFrame, feature_columns: list[str]) -> list[dict]:
    """
    How much each feature actually varies, and how often it was measured
    rather than imputed from a city-wide prior.

    This matters more than it sounds. OpenStreetMap records a `lit` tag for
    roughly a tenth of mapped Delhi, so `lighting_score` is the city prior
    almost everywhere -- it barely varies, and it cannot carry information
    the model doesn't have. A metrics table that reports only accuracy would
    hide that completely.
    """
    rows = []
    for name in feature_columns:
        if name not in df.columns:
            continue
        values = df[name].to_numpy(dtype=float)
        test = _OBSERVATION_TESTS.get(name)
        observed = test(df) if test else None
        rows.append(
            {
                "feature": name,
                "mean": round(float(np.mean(values)), 4),
                "std": round(float(np.std(values)), 4),
                "observed_share": (
                    round(float(np.mean(observed)), 4) if observed is not None else 1.0
                ),
            }
        )
    return rows


def spatial_split(
    df: pd.DataFrame, seed: int = RANDOM_SEED
) -> dict[str, pd.DataFrame]:
    """
    Split rows into fit / calibrate / select / test by whole grid cell.

    Splitting rows at random would scatter near-duplicates -- samples from the
    same cell share their lighting, surveillance and footpath measurements --
    across both sides of the split, inflating the test score substantially.
    Holding out whole cells answers the question that actually matters: how
    does this behave in a part of the city it has never seen?
    """
    rng = np.random.default_rng(seed)
    cells = df["area_code"].unique()
    rng.shuffle(cells)

    boundaries = np.cumsum([SPLIT_FRACTIONS[k] for k in ("fit", "calibrate", "select")])
    cut_points = (boundaries * len(cells)).astype(int)
    groups = np.split(cells, cut_points)
    names = ("fit", "calibrate", "select", "test")
    return {
        name: df[df["area_code"].isin(set(group))].reset_index(drop=True)
        for name, group in zip(names, groups, strict=False)
    }


def train_model(
    df: pd.DataFrame,
    feature_columns: list[str],
    provenance: dict | None = None,
    seed: int = RANDOM_SEED,
) -> tuple[SafetyModel, dict]:
    """Fit, calibrate, select a calibration method, and evaluate once on test."""
    classes = list(RISK_LABELS)
    splits = spatial_split(df, seed=seed)
    for name, part in splits.items():
        if part.empty:
            raise ValueError(f"The '{name}' split is empty -- not enough distinct cells sampled.")
    log.info(
        "Spatial split (rows / cells): %s",
        {k: f"{len(v)} / {v['area_code'].nunique()}" for k, v in splits.items()},
    )

    def xy(part: pd.DataFrame):
        return part[feature_columns], part["label"].to_numpy()

    X_fit, y_fit = xy(splits["fit"])
    X_cal, y_cal = xy(splits["calibrate"])
    X_sel, y_sel = xy(splits["select"])
    X_test, y_test = xy(splits["test"])

    base = RandomForestClassifier(
        n_estimators=400,
        max_depth=14,
        min_samples_leaf=8,
        max_features="sqrt",
        class_weight="balanced_subsample",
        random_state=seed,
        n_jobs=-1,
    )
    base.fit(X_fit, y_fit)

    # Calibrate the already-fitted forest on data it never saw. That is the
    # only way to get an honest calibration map out of a model that memorises
    # its own training set -- calibrating on the fitting split would just
    # learn to reproduce its overconfidence.
    candidates = {}
    for method in ("isotonic", "sigmoid"):
        frozen = _frozen(base)
        calibrator = (
            CalibratedClassifierCV(frozen, method=method)
            if frozen is not base
            else CalibratedClassifierCV(base, method=method, cv="prefit")
        )
        calibrator.fit(X_cal, y_cal)
        candidates[method] = calibrator

    # Choose on the `select` split -- untouched by both the forest and the
    # calibrators -- so the choice cannot leak into the reported test metrics.
    selection = {
        method: evaluate(_proba(model, X_sel, classes), y_sel, classes)
        for method, model in candidates.items()
    }
    selection["uncalibrated"] = evaluate(_proba(base, X_sel, classes), y_sel, classes)

    # Log loss rewards being both accurate and honest about uncertainty, which
    # is precisely what we want from a confidence number a person will act on.
    best_method = min(candidates, key=lambda m: selection[m]["log_loss"])
    chosen = candidates[best_method]
    log.info(
        "Calibration selected: %s (select log-loss %.4f vs %s %.4f, uncalibrated %.4f)",
        best_method,
        selection[best_method]["log_loss"],
        *next(
            (m, selection[m]["log_loss"]) for m in candidates if m != best_method
        ),
        selection["uncalibrated"]["log_loss"],
    )

    # --- Final, single evaluation on the held-out test cells ---------------
    probability_columns = [f"p_{c.lower()}" for c in classes]
    true_probabilities = (
        splits["test"][probability_columns].to_numpy()
        if all(c in df.columns for c in probability_columns)
        else None
    )
    test_proba = _proba(chosen, X_test, classes)
    test_metrics = evaluate(test_proba, y_test, classes, true_probabilities)
    ceiling = test_metrics.get("reference", {}).get("accuracy")

    importance = permutation_importance(
        chosen, X_test, y_test, n_repeats=5, random_state=seed, n_jobs=-1,
        scoring="neg_log_loss",
    )
    feature_importance = sorted(
        (
            {"feature": name, "importance": round(float(mean), 5)}
            for name, mean in zip(feature_columns, importance.importances_mean, strict=False)
        ),
        key=lambda item: item["importance"],
        reverse=True,
    )

    predictions = np.array(classes)[test_proba.argmax(axis=1)]
    metrics = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model_version": __version__,
        "calibration_method": best_method,
        "n_features": len(feature_columns),
        "feature_columns": list(feature_columns),
        "split_sizes": {k: int(len(v)) for k, v in splits.items()},
        "split_cells": {k: int(v["area_code"].nunique()) for k, v in splits.items()},
        "test": test_metrics,
        "bayes_optimal_accuracy": ceiling,
        "accuracy_vs_ceiling": (
            round(test_metrics["accuracy"] / ceiling, 4) if ceiling else None
        ),
        "calibration_comparison_on_select_split": {
            method: {k: round(v, 5) for k, v in scores.items() if k != "calibration_bins"}
            for method, scores in selection.items()
        },
        "per_class": classification_report(
            y_test, predictions, labels=classes, output_dict=True, zero_division=0
        ),
        "confusion_matrix": {
            "labels": classes,
            "counts": confusion_matrix(y_test, predictions, labels=classes).tolist(),
        },
        "permutation_importance": feature_importance,
        "feature_diagnostics": feature_diagnostics(df, feature_columns),
        "label_distribution": df["label"].value_counts(normalize=True).round(4).to_dict(),
        "provenance": provenance or {},
    }

    model = SafetyModel(
        estimator=chosen,
        feature_columns=list(feature_columns),
        classes=classes,
        trained_at=metrics["generated_at"],
        provenance=provenance or {},
        metrics={k: v for k, v in metrics.items() if k != "permutation_importance"},
    )
    return model, metrics


def _frozen(estimator):
    """
    Wrap a fitted estimator so CalibratedClassifierCV calibrates it as-is.

    scikit-learn 1.6 introduced `FrozenEstimator` for this and removed the
    old `cv="prefit"` spelling in 1.8, so both are supported rather than
    pinning the project to one scikit-learn generation.
    """
    try:
        from sklearn.frozen import FrozenEstimator
    except ImportError:  # pragma: no cover - scikit-learn < 1.6
        return estimator
    return FrozenEstimator(estimator)


def _proba(estimator, X: pd.DataFrame, classes: list[str]) -> np.ndarray:
    """predict_proba re-ordered into `classes` order, whatever the estimator's own order is."""
    raw = estimator.predict_proba(X)
    order = [list(estimator.classes_).index(c) for c in classes]
    return raw[:, order]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save_model(model: SafetyModel, metrics: dict | None = None) -> None:
    joblib.dump(model, MODEL_PATH)
    if metrics is not None:
        MODEL_METRICS_PATH.write_text(json.dumps(metrics, indent=2, default=str))


def load_model() -> SafetyModel | None:
    """Load the model bundle, or None if it hasn't been trained yet."""
    if not MODEL_PATH.exists():
        return None
    try:
        model = joblib.load(MODEL_PATH)
    except Exception as exc:  # noqa: BLE001 - a stale/corrupt artifact must not crash startup
        log.warning("Could not load model at %s (%s); it will need retraining.", MODEL_PATH, exc)
        return None
    if not isinstance(model, SafetyModel):
        log.warning(
            "Model artifact at %s predates the versioned bundle format and has no feature "
            "contract; retrain with `python train_model.py`.",
            MODEL_PATH,
        )
        return None
    return model
