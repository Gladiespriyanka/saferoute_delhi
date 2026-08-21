"""
Standalone entry point to (re)generate synthetic data and train the model.

    python train_model.py [--n-samples 6000]
"""
from __future__ import annotations

import argparse

from app.data_gen import generate_training_dataset
from app.config import TRAINING_DATA_PATH
from app.model import save_model, train_model


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the SafeRoute Delhi safety model.")
    parser.add_argument("--n-samples", type=int, default=6000, help="Number of synthetic training samples")
    args = parser.parse_args()

    print(f"Generating {args.n_samples} synthetic training samples...")
    df = generate_training_dataset(n_samples=args.n_samples)
    df.to_csv(TRAINING_DATA_PATH, index=False)
    print(f"Saved training data to {TRAINING_DATA_PATH}")

    print("Training + calibrating model...")
    model, metrics = train_model(df)
    save_model(model)

    print("Training complete.")
    print(f"  Test accuracy:    {metrics['test_accuracy']:.4f}")
    print(f"  Mean confidence:  {metrics['mean_confidence']:.4f}")
    print(f"  Train / test size: {metrics['n_train']} / {metrics['n_test']}")
    print(f"  Classes: {metrics['class_order']}")


if __name__ == "__main__":
    main()
