"""Compare frozen classifiers on a separately annotated test set."""
import argparse
import json
import os
from collections import Counter
from pathlib import Path

import matplotlib
import numpy as np
import torch

from app.commitment_model import LABELS, REVIEW_THRESHOLD, classifier, predict, trained_review_threshold
from commitment_metrics import evaluate
from prepare_fresh_review import normalize


def load_rows(paths):
    return [json.loads(line) for path in paths for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", type=Path, nargs="+", required=True)
    parser.add_argument("--training-data", type=Path, nargs="+", required=True)
    parser.add_argument("--models", type=Path, nargs="+", required=True)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--review-queue", type=Path, help="Verify that every row in the review queue has a decision.")
    parser.add_argument("--provisional", action="store_true", help="Mark the chart as using provisional labels.")
    args = parser.parse_args()
    development = [row for row in load_rows(args.training_data) if row["label"] and row["split"] != "test"]
    train = [row for row in development if row["split"] == "train"]
    raw_test = load_rows(args.test)
    if args.review_queue:
        queue = load_rows([args.review_queue])
        reviewed = {row["id"]: row for row in raw_test}
        queue_ids = {row["id"] for row in queue}
        if len(queue_ids) != len(queue) or len(reviewed) != len(raw_test) or reviewed.keys() != queue_ids:
            parser.error("The review must contain one decision for every queue row.")
        for original in queue:
            decision = reviewed[original["id"]]
            if "label" not in decision or decision["label"] not in (*LABELS, None):
                parser.error(f"Invalid review label for {original['id']}.")
            if any(decision.get(key) != value for key, value in original.items() if key != "label"):
                parser.error(f"Review changed original transcript evidence at {original['id']}.")
        raw_test = [reviewed[row["id"]] for row in queue]
    if any(row["split"] != "test" for row in raw_test):
        parser.error("The evaluation input must contain test rows only.")
    test = [row for row in raw_test if row["label"]]
    if not test or not train:
        parser.error("Provide labelled training and test rows.")
    keys = [normalize(row["quote"]) for row in test]
    development_keys = {normalize(row["quote"]) for row in development}
    development_groups = {(row["source"], row["group"]) for row in development}
    # A full-recording review preserves repeated turns, such as acknowledgements.
    # Compare substantive text across development and test and retain the meeting-level check.
    checked_keys = [key for key in keys if len(key.split()) >= 5] if args.review_queue else keys
    repeated = not args.review_queue and len(set(keys)) != len(keys)
    if repeated or development_keys.intersection(checked_keys) or any((row["source"], row["group"]) in development_groups for row in test):
        parser.error("Duplicate test text or overlap with training or validation data.")
    torch.set_num_threads(min(4, torch.get_num_threads()))
    results = {}
    for path in args.models:
        os.environ["PROMISE_KEEPER_CLASSIFIER"] = str(path.resolve())
        classifier.cache_clear()
        probabilities = np.array([[item[label] for label in LABELS] for item in predict(test)])
        threshold = trained_review_threshold(str(path.resolve()))
        samples = dict.fromkeys(row["sample"] for row in test) if args.review_queue else ("random", "challenge")
        for sample in samples:
            mask = np.array([row["sample"] == sample for row in test])
            rows = [row for row in test if row["sample"] == sample]
            if not rows:
                continue
            support = Counter(row["label"] for row in rows)
            truth = np.array([LABELS.index(row["label"]) for row in rows])
            report = evaluate({"train": train, "test": rows}, truth, probabilities[mask],
                              REVIEW_THRESHOLD if threshold is None else threshold, legacy_routing=threshold is None)
            values = report["slices"]["all"]
            classification, review = values["classification"], values["review_routing"]
            print(f"{path.name} | {sample} | n={len(rows)} | support {dict(support)}")
            print(f"Accuracy {classification['accuracy']:.3f}, macro F1 {classification['macro avg']['f1-score']:.3f}, "
                  f"review precision {review['precision']}, recall {review['recall']}, AP {review['average_precision']}, "
                  f"TP/FP/FN {review['tp']}/{review['fp']}/{review['fn']}")
            results.setdefault(sample, []).append((path.name, classification, review))
    print(f"Excluded {len(raw_test) - len(test)} rows marked unusable or out of scope.")
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, len(results), squeeze=False, figsize=(7 * len(results), 4.8), layout="constrained")
    groups = np.arange(4)
    colors = ["#617493", "#166b9e", "#3b785d"]
    for axis, (sample, entries) in zip(axes[0], results.items(), strict=True):
        width = .75 / len(entries)
        for index, (name, classification, review) in enumerate(entries):
            values = [classification["macro avg"]["f1-score"], review["precision"], review["recall"], review["average_precision"]]
            offset = (index - (len(entries) - 1) / 2) * width
            bars = axis.bar(groups + offset, [value or 0 for value in values], width, label=name, color=colors[index % len(colors)])
            axis.bar_label(bars, labels=["N/A" if v is None else f"{v:.2f}" for v in values], fontsize=8, padding=2)
        axis.set(xticks=groups, xticklabels=["Macro F1", "Review precision", "Review recall", "Average precision"],
                 ylim=(0, 1.12), title=f"{sample.capitalize()} sample")
        axis.legend(fontsize=8, loc="upper left")
    label_source = "provisional transcript labels" if args.provisional or not args.review_queue else "reviewed meeting transcripts"
    fig.suptitle(f"Promise Keeper candidates | {label_source}")
    fig.savefig(args.graph, dpi=160)
    plt.close(fig)


if __name__ == "__main__":
    main()
