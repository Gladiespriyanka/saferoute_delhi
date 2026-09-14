"""Model training, evaluation and the feature contract."""
from __future__ import annotations

import numpy as np
import pytest

from app.config import RISK_LABELS
from app.model import (
    FeatureContractError,
    SafetyModel,
    evaluate,
    expected_calibration_error,
    load_model,
    multiclass_brier,
    multiclass_log_loss,
    spatial_split,
)

CLASSES = list(RISK_LABELS)


# --- Metric correctness ----------------------------------------------------


def test_ece_is_zero_for_a_perfectly_calibrated_predictor():
    probabilities = np.tile([0.8, 0.15, 0.05], (100, 1))
    correct = np.array([1.0] * 80 + [0.0] * 20)
    ece, mce, bins = expected_calibration_error(probabilities, correct)
    assert ece == pytest.approx(0.0, abs=1e-9)
    assert mce == pytest.approx(0.0, abs=1e-9)
    assert len(bins) == 1


def test_ece_catches_overconfidence():
    """A model claiming 99% while being right half the time must score badly."""
    probabilities = np.tile([0.99, 0.005, 0.005], (100, 1))
    correct = np.array([1.0] * 50 + [0.0] * 50)
    ece, _, _ = expected_calibration_error(probabilities, correct)
    assert ece == pytest.approx(0.49, abs=0.01)


def test_brier_is_zero_for_perfect_predictions():
    probabilities = np.eye(3)
    assert multiclass_brier(probabilities, np.array(CLASSES), CLASSES) == pytest.approx(0.0)


def test_log_loss_respects_our_class_order_not_sklearn_s_sorted_order():
    """
    Our class order (Safe, Moderate, Unsafe) is semantic, not alphabetical.
    sklearn's log_loss assumes sorted columns, so a naive call scores the
    probabilities against permuted classes -- a bug that once reported 2.1
    for a model whose real log loss was 0.45.
    """
    confident_and_right = np.tile([0.98, 0.01, 0.01], (10, 1))
    y_true = np.array(["Safe"] * 10)
    assert multiclass_log_loss(confident_and_right, y_true, CLASSES) < 0.05

    confident_and_wrong = np.tile([0.01, 0.01, 0.98], (10, 1))
    assert multiclass_log_loss(confident_and_wrong, y_true, CLASSES) > 3.0


def test_evaluate_reports_reference_values_when_true_probabilities_are_known():
    rng = np.random.default_rng(0)
    truth = rng.dirichlet([2, 2, 2], size=400)
    labels = np.array(CLASSES)[
        [rng.choice(3, p=row) for row in truth]
    ]
    metrics = evaluate(truth, labels, CLASSES, true_probabilities=truth)
    assert metrics["reference"]["accuracy"] == pytest.approx(metrics["accuracy"], abs=0.08)
    assert metrics["reference"]["log_loss"] == pytest.approx(metrics["log_loss"], abs=1e-9)


# --- Splitting -------------------------------------------------------------


def test_spatial_split_never_shares_a_cell_between_parts(training_frame):
    """
    The whole point: samples from one cell share their measurements, so a
    random row split would leak near-duplicates into the test set.
    """
    splits = spatial_split(training_frame, seed=7)
    cells = {name: set(part["area_code"]) for name, part in splits.items()}
    names = list(cells)
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            assert not cells[left] & cells[right], f"{left} and {right} share cells"


def test_spatial_split_keeps_every_row(training_frame):
    splits = spatial_split(training_frame, seed=7)
    assert sum(len(part) for part in splits.values()) == len(training_frame)


def test_spatial_split_is_deterministic(training_frame):
    first = spatial_split(training_frame, seed=7)["test"]["area_code"].tolist()
    second = spatial_split(training_frame, seed=7)["test"]["area_code"].tolist()
    assert first == second


# --- The trained model -----------------------------------------------------


def test_model_is_a_versioned_bundle(trained_model):
    assert isinstance(trained_model, SafetyModel)
    assert trained_model.feature_columns
    assert trained_model.classes == CLASSES
    assert trained_model.trained_at


def test_model_rejects_a_row_missing_a_contracted_feature(trained_model):
    """Silently scoring the wrong columns is the failure mode this prevents."""
    row = dict.fromkeys(trained_model.feature_columns, 0.5)
    row.pop(trained_model.feature_columns[0])
    with pytest.raises(FeatureContractError):
        trained_model.predict_proba([row])


def test_scores_are_well_formed(trained_model):
    row = dict.fromkeys(trained_model.feature_columns, 0.5)
    result = trained_model.score_rows([row])[0]
    assert result["label"] in CLASSES
    assert 1 / 3 <= result["confidence"] <= 1.0
    assert 0.0 <= result["margin"] <= 1.0
    assert 0.0 <= result["risk_score"] <= 1.0
    assert sum(result["class_probabilities"].values()) == pytest.approx(1.0, abs=1e-6)


def test_risk_score_orders_the_classes(trained_model):
    """Safe must map to a lower scalar risk than Unsafe."""
    base = dict.fromkeys(trained_model.feature_columns, 0.5)
    certain_safe = dict(base)
    certain_unsafe = dict(base)
    for name, value in (("lighting_score", 1.0), ("infra_score", 1.0),
                        ("isolation_index", 0.0), ("time_of_day_risk", 0.15)):
        if name in certain_safe:
            certain_safe[name] = value
    for name, value in (("lighting_score", 0.0), ("infra_score", 0.0),
                        ("isolation_index", 1.0), ("time_of_day_risk", 0.95)):
        if name in certain_unsafe:
            certain_unsafe[name] = value
    safe = trained_model.score_rows([certain_safe])[0]
    unsafe = trained_model.score_rows([certain_unsafe])[0]
    assert safe["risk_score"] < unsafe["risk_score"]


def test_model_generalises_and_is_calibrated(trained_model):
    """
    Guards the two claims the model card makes. Thresholds are deliberately
    loose -- this runs on a small synthetic city -- but they would catch a
    regression that broke the split, the calibration or the label sampling.
    """
    test = trained_model.metrics["test"]
    ceiling = test["reference"]["accuracy"]
    assert test["accuracy"] > 0.55
    assert test["accuracy"] <= ceiling + 0.1, "accuracy above the noise ceiling implies leakage"
    assert test["ece"] < 0.15
    # A calibrated model's average confidence should track its accuracy.
    assert abs(test["mean_confidence"] - test["accuracy"]) < 0.15


def test_saved_model_round_trips(trained_model):
    loaded = load_model()
    assert loaded is not None
    assert loaded.feature_columns == trained_model.feature_columns
    row = dict.fromkeys(trained_model.feature_columns, 0.5)
    assert loaded.score_rows([row])[0]["label"] == trained_model.score_rows([row])[0]["label"]


def test_predict_proba_columns_follow_our_class_order_not_sklearn_s(trained_model):
    """
    Regression test. scikit-learn sorts classes alphabetically (Moderate,
    Safe, Unsafe); ours are ordered by severity (Safe, Moderate, Unsafe).
    Reading the raw output positionally swapped Safe and Moderate on every
    prediction, which inverted the whole time-of-day relationship -- 2am
    scored safer than midday.
    """
    row = dict.fromkeys(trained_model.feature_columns, 0.5)
    probabilities = trained_model.predict_proba([row])[0]
    estimator_probabilities = trained_model.estimator.predict_proba(
        __import__("pandas").DataFrame([row])[trained_model.feature_columns]
    )[0]
    estimator_classes = list(trained_model.estimator.classes_)

    for index, label in enumerate(trained_model.classes):
        assert probabilities[index] == pytest.approx(
            estimator_probabilities[estimator_classes.index(label)]
        )


def test_risk_score_increases_with_time_of_day_risk(trained_model):
    """Midday must score safer than 2am, all else equal."""
    if "time_of_day_risk" not in trained_model.feature_columns:  # pragma: no cover
        pytest.skip("time_of_day_risk is not part of this feature contract")
    from app.features import cyclic_time_features, time_of_day_risk

    def score_at(hour: int) -> float:
        row = dict.fromkeys(trained_model.feature_columns, 0.5)
        row.update(
            {k: v for k, v in cyclic_time_features(hour, 2).items() if k in row}
        )
        row["time_of_day_risk"] = time_of_day_risk(hour)
        return trained_model.score_rows([row])[0]["risk_score"]

    assert score_at(2) > score_at(13)
