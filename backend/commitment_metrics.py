from collections import Counter

import numpy as np
from sklearn.metrics import average_precision_score, classification_report, confusion_matrix, fbeta_score, precision_recall_curve, precision_recall_fscore_support

from app.commitment_model import COMMITMENT_LABELS, LABELS, REVIEW_THRESHOLD


def commitment_targets(truth, probabilities):
    indices = [LABELS.index(label) for label in COMMITMENT_LABELS]
    return np.isin(truth, indices), probabilities[:, indices].sum(axis=1)


def tune_threshold(truth, probabilities, beta=1.0):
    gold, ranking = commitment_targets(truth, probabilities)
    if not gold.any() or gold.all():
        raise ValueError("Threshold tuning needs both commitments and other utterances in validation.")
    # Choose a point between observed scores so numerical differences after export
    # do not flip a validation example sitting exactly at the cutoff.
    unique = np.unique(ranking.astype(np.float64))
    cutoffs = np.r_[unique[0] / 2, (unique[:-1] + unique[1:]) / 2]
    return max(cutoffs, key=lambda value: (fbeta_score(gold, ranking >= value, beta=beta), value)).item()


def evaluate(partitions, truth, probabilities, threshold=REVIEW_THRESHOLD, *, legacy_routing=False):
    predictions = probabilities.argmax(axis=1)
    commitment, ranking = commitment_targets(truth, probabilities)
    reviewed = ranking >= threshold
    if legacy_routing:
        reviewed |= np.isin(predictions, [LABELS.index(label) for label in COMMITMENT_LABELS])
    majority = Counter(row["label"] for row in partitions["train"]).most_common(1)[0][0]
    indices = list(range(len(LABELS)))
    sources = np.array([row["source"] for row in partitions["test"]])
    report = {
        "labels": LABELS,
        "split_support": {
            split: dict(Counter(row["label"] for row in rows)) for split, rows in partitions.items()
        },
        "review_threshold": threshold,
        "slices": {},
    }
    for source in [None, *sorted(set(sources))]:
        mask = np.ones(len(truth), dtype=bool) if source is None else sources == source
        gold, routed = commitment[mask], reviewed[mask]
        tn, fp, fn, tp = (int(value) for value in confusion_matrix(gold, routed, labels=[False, True]).ravel())
        precision, recall, f1, _ = precision_recall_fscore_support(gold, routed, average="binary", zero_division=0)
        report["slices"][source or "all"] = {
            "classification": classification_report(truth[mask], predictions[mask],
                labels=indices, target_names=LABELS, zero_division=0, output_dict=True),
            "confusion_matrix": confusion_matrix(truth[mask], predictions[mask], labels=indices).tolist(),
            "majority_baseline": {"label": majority, "accuracy": float((truth[mask] == LABELS.index(majority)).mean())},
            "review_routing": {
                "precision": float(precision) if tp + fp else None,
                "recall": float(recall) if tp + fn else None,
                "f1": float(f1) if tp + fp + fn else None,
                "average_precision": float(average_precision_score(gold, ranking[mask])) if gold.any() and not gold.all() else None,
                "tp": tp, "fp": fp, "fn": fn, "tn": tn,
                "requests_routed": int((routed & (truth[mask] == LABELS.index("request_assignment"))).sum()),
                "review_fraction": float(routed.mean()),
            },
        }
    return report


def show_results(report, truth, probabilities, history, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    for source, results in report["slices"].items():
        classification = results["classification"]
        routing = results["review_routing"]
        print(f"\n{source} | accuracy {classification['accuracy']:.1%} | macro F1 {classification['macro avg']['f1-score']:.3f}")
        print(f"Majority baseline {results['majority_baseline']['accuracy']:.1%}")
        print(f"Review threshold {report['review_threshold']:.3f} | precision {routing['precision']} | recall {routing['recall']} | average precision {routing['average_precision']}")
        print(f"Review true positives {routing['tp']} | false positives {routing['fp']} | missed commitments {routing['fn']} | requests routed {routing['requests_routed']}")
        for label in LABELS:
            metrics = classification[label]
            print(f"{label:22} precision {metrics['precision']:.3f}  recall {metrics['recall']:.3f}  F1 {metrics['f1-score']:.3f}  support {metrics['support']:.0f}")

    figure, axes = plt.subplots(1, 3, figsize=(16, 5), layout="constrained")
    matrix, curve, learning = axes
    counts = report["slices"]["all"]["confusion_matrix"]
    image = matrix.imshow(counts, cmap="Blues")
    figure.colorbar(image, ax=matrix, shrink=.7)
    names = [label.replace("_", " ") for label in LABELS]
    matrix.set(xticks=range(len(names)), yticks=range(len(names)), yticklabels=names,
               title="Test confusion matrix", xlabel="Predicted", ylabel="Actual")
    matrix.set_xticklabels(names, rotation=40, ha="right")
    for row, values in enumerate(counts):
        for col, count in enumerate(values):
            matrix.text(col, row, count, ha="center", va="center", color="white" if image.norm(count) > .55 else "#172b44")

    gold, ranking = commitment_targets(truth, probabilities)
    precision, recall, _ = precision_recall_curve(gold, ranking)
    curve.step(recall, precision, where="post", color="#275bb7", label="Test curve")
    routed = report["slices"]["all"]["review_routing"]
    if routed["precision"] is not None and routed["recall"] is not None:
        curve.scatter(routed["recall"], routed["precision"], color="#b54c28", label="Validation-selected threshold")
    curve.legend(loc="upper right", fontsize=8)
    curve.set(xlim=(0, 1.02), ylim=(0, 1.04), title="Commitment review", xlabel="Recall", ylabel="Precision")

    visits = [entry for entry in history if "eval_macro_f1" in entry]
    learning.plot([entry["epoch"] for entry in visits], [entry["eval_macro_f1"] for entry in visits], "o-", color="#275bb7")
    learning.set(ylim=(0, 1), title="Validation macro F1", xlabel="Epoch", ylabel="Macro F1")
    figure.savefig(output / "evaluation.png", dpi=170)
    plt.close(figure)
