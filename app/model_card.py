"""
Renders the human-readable model card from the metrics dict.

Kept separate from training so the card can be regenerated from
`artifacts/model_metrics.json` alone, and so the exact wording of the
caveats lives in one reviewable place rather than being interpolated
through print statements.
"""
from __future__ import annotations

import json

from app.config import MODEL_METRICS_PATH


def _verdict(ece: float) -> str:
    if ece < 0.03:
        return "well calibrated - the reported confidence can be taken at face value"
    if ece < 0.07:
        return "usably calibrated - confidence is directionally right, treat exact values loosely"
    return "poorly calibrated - do not present the confidence number to users as a probability"


def render_model_card(metrics: dict) -> str:
    test = metrics["test"]
    ceiling = metrics.get("bayes_optimal_accuracy")
    provenance = metrics.get("provenance", {})
    lines: list[str] = []
    add = lines.append

    add("# SafeHerWay safety model - model card\n")
    add(f"Generated {metrics['generated_at']} - model version {metrics['model_version']}\n")

    add("## What it does\n")
    add(
        "Classifies a point on a walking route as **Safe / Moderate / Unsafe** at a given "
        "hour and day of week, and reports a calibrated probability for each class. The "
        "route-level score is an aggregate of its segments (weighted toward the worst one), "
        "which live weather and traffic context may then nudge within a hard cap.\n"
    )

    add("## Data\n")
    add("| Source | Value |")
    add("| --- | --- |")
    for key, value in provenance.items():
        add(f"| `{key}` | {value} |")
    add("")
    add(
        "Environmental features are **measured from OpenStreetMap**: streetlight and "
        "surveillance node density around each point, `lit=yes` tagged road share, footway "
        "length density, commercial activity density as a footfall proxy, and true distances "
        "to the nearest metro station, bus stop, hospital and police station.\n"
    )
    if provenance.get("crime_data", "").startswith("not configured"):
        add(
            "> **Crime data is not configured.** `crime_risk_index` is excluded from the "
            "feature set entirely and its composite weight redistributed over the measured "
            "features. This model has therefore been trained *only* on measured "
            "OpenStreetMap quantities - no crime figures were invented to fill the gap. "
            "See `data/README.md` to populate it.\n"
        )

    add("## Labels are bootstrapped, and that is the main limitation\n")
    add(
        "No ground-truth incident labels were available, so labels come from a transparent "
        "weighted composite of the measured features, pushed through an ordered logit and "
        "**sampled** rather than thresholded. That deliberately builds in irreducible "
        "uncertainty: places near a class boundary are genuine coin flips, which is what "
        "makes a calibrated confidence number meaningful instead of a uniform 0.99.\n"
    )
    add(
        "The consequence to keep in mind: **the model has learned a smoothed version of that "
        "formula as applied to real geography, not real safety outcomes.** It is a "
        "well-engineered prior over Delhi's walking infrastructure, not an empirical crime "
        "predictor. Replacing the composite with real incident and audit outcomes is the "
        "single highest-value next step.\n"
    )

    add("## Evaluation\n")
    add(
        "Rows are split by **whole grid cell**, never at random. Samples from the same "
        "~1.2 x 1.4 km cell share their infrastructure measurements, so a random row split "
        "would scatter near-duplicates across train and test and inflate the score. The "
        "four-way fit / calibrate / select / test split means the calibration method was "
        "chosen on data the calibrators never saw, and these test numbers were produced "
        "exactly once, on data nothing was selected with.\n"
    )
    reference = test.get("reference", {})
    add("| Metric | Model | Best achievable | Notes |")
    add("| --- | --- | --- | --- |")
    add(
        f"| Accuracy | {test['accuracy']:.4f} | "
        + (f"{reference['accuracy']:.4f} " if reference.get("accuracy") else "- ")
        + "| higher is better |"
    )
    add(f"| Macro F1 | {test['macro_f1']:.4f} | - | higher is better |")
    add(
        f"| Log loss | {test['log_loss']:.4f} | "
        + (f"{reference['log_loss']:.4f} " if reference.get("log_loss") else "- ")
        + "| lower is better |"
    )
    add(
        f"| Brier score | {test['brier']:.4f} | "
        + (f"{reference['brier']:.4f} " if reference.get("brier") else "- ")
        + "| lower is better |"
    )
    add(f"| Expected calibration error | {test['ece']:.4f} | 0 | lower is better |")
    add(f"| Max calibration error | {test['mce']:.4f} | 0 | lower is better |")
    add(
        f"| Mean confidence | {test['mean_confidence']:.4f} | {test['accuracy']:.4f} "
        "| should match accuracy |"
    )
    add("")
    add(f"Calibration method selected: **{metrics['calibration_method']}**.\n")
    add(
        "The *best achievable* column is not 1.0 (or 0.0). Labels are sampled from class "
        "probabilities, so a share of them is irreducibly unpredictable; these are the "
        "values a model that knew the true probabilities exactly would score. Judging "
        "accuracy against 100% would understate the model and judging log loss against 0 "
        "would be meaningless.\n"
    )
    add(f"**Calibration verdict:** {_verdict(test['ece'])}.\n")
    if ceiling:
        add(
            f"This model reaches **{metrics['accuracy_vs_ceiling']:.1%} of the achievable "
            f"accuracy ceiling** ({test['accuracy']:.3f} of a possible {ceiling:.3f}).\n"
        )

    add("### Calibration methods compared (on the selection split)\n")
    comparison = metrics.get("calibration_comparison_on_select_split", {})
    if comparison:
        add("| Method | Log loss | ECE | Brier | Accuracy |")
        add("| --- | --- | --- | --- | --- |")
        for method, scores in comparison.items():
            add(
                f"| {method} | {scores['log_loss']:.4f} | {scores['ece']:.4f} | "
                f"{scores['brier']:.4f} | {scores['accuracy']:.4f} |"
            )
        add("")

    add("### Per-class performance\n")
    add("| Class | Precision | Recall | F1 | Support |")
    add("| --- | --- | --- | --- | --- |")
    for label in metrics["confusion_matrix"]["labels"]:
        scores = metrics["per_class"].get(label)
        if scores:
            add(
                f"| {label} | {scores['precision']:.3f} | {scores['recall']:.3f} | "
                f"{scores['f1-score']:.3f} | {int(scores['support'])} |"
            )
    add("")

    add("### Confusion matrix (rows = actual, columns = predicted)\n")
    labels = metrics["confusion_matrix"]["labels"]
    add("| | " + " | ".join(labels) + " |")
    add("| --- |" + " --- |" * len(labels))
    for label, row in zip(labels, metrics["confusion_matrix"]["counts"], strict=False):
        add(f"| **{label}** | " + " | ".join(str(v) for v in row) + " |")
    add("")

    diagnostics = metrics.get("feature_diagnostics") or []
    if diagnostics:
        add("### What the features actually contain\n")
        add(
            "`observed` is the share of sampled points where the feature was really "
            "measured rather than imputed from a city-wide prior, and `std` is how much "
            "it varies across the city. A feature that is mostly imputed, or barely "
            "varies, cannot carry information the model does not have -- which an "
            "accuracy figure alone would hide completely.\n"
        )
        add("| Feature | Mean | Std | Observed |")
        add("| --- | --- | --- | --- |")
        for item in diagnostics:
            flag = " ⚠️" if item["observed_share"] < 0.5 or item["std"] < 0.05 else ""
            add(
                f"| `{item['feature']}` | {item['mean']:.3f} | {item['std']:.3f} | "
                f"{item['observed_share']:.0%}{flag} |"
            )
        add("")
        weak = [
            item["feature"]
            for item in diagnostics
            if item["observed_share"] < 0.5 or item["std"] < 0.05
        ]
        if weak:
            add(
                "> Flagged above: "
                + ", ".join(f"`{name}`" for name in weak)
                + ". These are thin in OpenStreetMap for Delhi. They are kept in the "
                "feature set because a caller can override them per segment with a "
                "first-hand observation, and because they are informative where they "
                "do exist -- but the model is not learning much from them, and the "
                "explanations say when a value was imputed rather than measured.\n"
            )

    if metrics.get("permutation_importance"):
        add("### Permutation importance (test split, neg. log loss)\n")
        add("| Feature | Importance |")
        add("| --- | --- |")
        for item in metrics["permutation_importance"]:
            add(f"| `{item['feature']}` | {item['importance']:+.5f} |")
        add("")

    add("## Known limitations\n")
    add(
        "- **OpenStreetMap coverage is uneven.** Central Delhi is mapped in far more detail "
        "than the outskirts, so a cell with no mapped streetlights is usually under-mapped "
        "rather than unlit. Sparse cells are shrunk toward the city mean and every "
        "prediction carries an `osm_coverage` score, but this remains the largest source of "
        "systematic error."
    )
    add(
        "- **Commercial density is a proxy for footfall**, not a measurement of it. It will "
        "understate residential streets that are busy with people but have no shops."
    )
    add(
        "- **The grid is not administrative wards.** ~1.2 x 1.4 km cells are a convenience, "
        "not a meaningful civic boundary."
    )
    add(
        "- **Not a substitute for judgment.** The output is a prior derived from "
        "infrastructure data. It does not observe the street right now, and should never be "
        "the only input to a decision about personal safety."
    )
    add("")
    return "\n".join(lines)


def main() -> int:
    """Regenerate the card from the saved metrics file."""
    if not MODEL_METRICS_PATH.exists():
        print(f"No metrics at {MODEL_METRICS_PATH}; run train_model.py first.")
        return 1
    from app.config import MODEL_CARD_PATH

    MODEL_CARD_PATH.write_text(render_model_card(json.loads(MODEL_METRICS_PATH.read_text())))
    print(f"Wrote {MODEL_CARD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
