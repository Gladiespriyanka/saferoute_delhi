"""
Build the training dataset from the real OpenStreetMap extract, fit and
calibrate the safety model, and write a model card.

    python train_model.py                  # 20,000 sampled points
    python train_model.py --n-samples 50000

Requires `python fetch_osm_data.py` to have been run at least once.

Writes:
    artifacts/safety_model.joblib   the model bundle (estimator + feature contract)
    artifacts/model_metrics.json    every metric, in full
    artifacts/model_card.md         the human-readable summary
    artifacts/training_data.csv     the sampled dataset
"""
from __future__ import annotations

import argparse
import logging
import sys

from app.config import MODEL_CARD_PATH, MODEL_METRICS_PATH, RANDOM_SEED, TRAINING_DATA_PATH
from app.dataset import MissingOsmDataError, build_training_dataset, load_delhi_data
from app.features import model_feature_columns
from app.model import save_model, train_model
from app.model_card import render_model_card


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-samples", type=int, default=20_000,
                        help="Number of points sampled across the city")
    parser.add_argument("--seed", type=int, default=RANDOM_SEED)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s: %(message)s"
    )

    try:
        data = load_delhi_data(allow_synthetic=False)
    except MissingOsmDataError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    provenance = data.describe()
    print("Data sources")
    for key, value in provenance.items():
        print(f"  {key:18s} {value}")

    if not data.with_crime:
        print(
            "\nNOTE: district crime data is not configured, so `crime_risk_index` is "
            "excluded from the feature set entirely and its composite weight is "
            "redistributed over the measured features. See data/README.md.\n"
        )

    print(f"Sampling {args.n_samples} points...")
    df = build_training_dataset(data, n_samples=args.n_samples, seed=args.seed)
    df.to_csv(TRAINING_DATA_PATH, index=False)
    print(f"Wrote training data to {TRAINING_DATA_PATH}")

    feature_columns = model_feature_columns(data.with_crime)
    print(f"Training on {len(feature_columns)} features...")
    model, metrics = train_model(df, feature_columns, provenance=provenance, seed=args.seed)
    save_model(model, metrics)

    MODEL_CARD_PATH.write_text(render_model_card(metrics))

    test = metrics["test"]
    ceiling = metrics["bayes_optimal_accuracy"]
    print("\nHeld-out test performance (whole grid cells never seen in training)")
    print(f"  accuracy             {test['accuracy']:.4f}"
          + (f"   (Bayes-optimal ceiling {ceiling:.4f})" if ceiling else ""))
    print(f"  macro F1             {test['macro_f1']:.4f}")
    print(f"  log loss             {test['log_loss']:.4f}")
    print(f"  Brier score          {test['brier']:.4f}")
    print(f"  calibration error    ECE {test['ece']:.4f} / max {test['mce']:.4f}")
    print(f"  mean confidence      {test['mean_confidence']:.4f}"
          f"   (vs accuracy {test['accuracy']:.4f})")
    print(f"  calibration method   {metrics['calibration_method']}")
    print("\nTop features by permutation importance:")
    for item in metrics["permutation_importance"][:6]:
        print(f"  {item['feature']:24s} {item['importance']:+.5f}")
    print(f"\nModel card: {MODEL_CARD_PATH}")
    print(f"Full metrics: {MODEL_METRICS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
