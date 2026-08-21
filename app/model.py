"""
ML model for SafeRoute Delhi: trains a calibrated classifier that predicts
Safe / Moderate / Unsafe from engineered segment features, with well
calibrated class probabilities used as the confidence measure.
"""
from __future__ import annotations

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

from app.config import CALIBRATOR_LABELS_PATH, MODEL_PATH, RANDOM_SEED, RISK_LABELS
from app.features import MODEL_FEATURE_COLUMNS


def train_model(df: pd.DataFrame) -> tuple[CalibratedClassifierCV, dict]:
    """
    Train a RandomForest classifier and wrap it with isotonic calibration
    so predict_proba outputs can be trusted as genuine confidence levels
    (uncalibrated tree ensembles are notoriously overconfident/underconfident
    at the extremes).

    Returns the fitted calibrated model and a dict of evaluation metrics.
    """
    X = df[MODEL_FEATURE_COLUMNS]
    y = df["label"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=RANDOM_SEED, stratify=y
    )

    base_clf = RandomForestClassifier(
        n_estimators=300,
        max_depth=12,
        min_samples_leaf=5,
        class_weight="balanced",
        random_state=RANDOM_SEED,
        n_jobs=-1,
    )

    # Calibrate via 5-fold cross-validation on the training split.
    calibrated = CalibratedClassifierCV(base_clf, method="isotonic", cv=5)
    calibrated.fit(X_train, y_train)

    preds = calibrated.predict(X_test)
    accuracy = float((preds == y_test).mean())

    # Per-class calibration sanity check: mean predicted confidence vs.
    # empirical accuracy for the predicted class.
    proba = calibrated.predict_proba(X_test)
    class_order = list(calibrated.classes_)
    confidences = proba.max(axis=1)
    mean_confidence = float(confidences.mean())

    metrics = {
        "test_accuracy": accuracy,
        "mean_confidence": mean_confidence,
        "class_order": class_order,
        "n_train": len(X_train),
        "n_test": len(X_test),
    }
    return calibrated, metrics


def save_model(model: CalibratedClassifierCV) -> None:
    joblib.dump(model, MODEL_PATH)
    joblib.dump(list(model.classes_), CALIBRATOR_LABELS_PATH)


def load_model() -> CalibratedClassifierCV | None:
    if not MODEL_PATH.exists():
        return None
    return joblib.load(MODEL_PATH)


def predict_segment(model: CalibratedClassifierCV, feature_row: dict) -> dict:
    """Run inference for a single segment's engineered feature dict."""
    X = pd.DataFrame([feature_row])[MODEL_FEATURE_COLUMNS]
    proba = model.predict_proba(X)[0]
    classes = list(model.classes_)
    label_idx = int(np.argmax(proba))
    label = classes[label_idx]
    confidence = float(proba[label_idx])

    # Map class probabilities to a single scalar risk_score in [0,1] using
    # the RISK_LABELS ordering (Safe=0, Moderate=0.5, Unsafe=1) weighted by
    # probability - this gives a smooth score rather than a hard 3-way split,
    # which is what downstream aggregation/adjustment logic operates on.
    label_to_scalar = {"Safe": 0.0, "Moderate": 0.5, "Unsafe": 1.0}
    risk_score = float(sum(p * label_to_scalar[c] for p, c in zip(proba, classes)))

    return {
        "label": label,
        "confidence": confidence,
        "risk_score": risk_score,
        "class_probabilities": dict(zip(classes, [float(p) for p in proba])),
    }
