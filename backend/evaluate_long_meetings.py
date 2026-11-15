"""Measure candidate workload on full ICSI meetings and accuracy on labelled turns."""
import argparse
import os
import time
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

import matplotlib
import numpy as np
import torch

from app.commitment_model import COMMITMENT_LABELS, DEFAULT_MODEL, classifier, commitment_score, predict, routes_to_review
from evaluate_commitments import load_rows
from prepare_fresh_review import meeting_candidates


def main():
    root = DEFAULT_MODEL.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", type=Path, required=True)
    parser.add_argument("--meetings", nargs="+", required=True)
    parser.add_argument("--graph", type=Path, required=True)
    args = parser.parse_args()
    labels = {
        row["id"]: row for row in load_rows([root / "fresh-test-assistant.jsonl", root / "fresh-test-request-labels.jsonl"])
        if row["label"] and row["source"] == "icsi"
    }
    transcripts = {name: [] for name in args.meetings}
    with ZipFile(root / "mrda.zip") as archive:
        for row in meeting_candidates(archive, min_words=1):
            if row["group"] in transcripts:
                transcripts[row["group"]].append(row)
    if any(not rows for rows in transcripts.values()):
        parser.error("Use ICSI meeting IDs from the test partition.")
    torch.set_num_threads(min(4, torch.get_num_threads()))
    records = []
    for model in args.models:
        os.environ["PROMISE_KEEPER_CLASSIFIER"] = str(model.resolve())
        classifier.cache_clear()
        workload, tally = [], Counter()
        for name, rows in transcripts.items():
            started = time.perf_counter()
            probabilities = predict(rows)
            selected = [routes_to_review(max(scores, key=scores.__getitem__), scores) for scores in probabilities]
            checked = 0
            for row, flagged in zip(rows, selected, strict=True):
                if label := labels.get(row["id"]):
                    if row["quote"] != label["quote"]:
                        raise ValueError(f"Transcript evidence differs for {row['id']}")
                    checked += 1
                    positive = label["label"] in COMMITMENT_LABELS
                    outcome = ("tp" if flagged else "fn") if positive else ("fp" if flagged else "tn")
                    tally[outcome] += 1
            workload.append(sum(selected))
            print(f"{model.name} | {name} | {len(rows)} turns | {checked} labelled | "
                  f"{workload[-1]} flagged | {time.perf_counter() - started:.1f} s", flush=True)
        print(f"{model.name} | checked TP/FP/FN/TN {tally['tp']}/{tally['fp']}/{tally['fn']}/{tally['tn']}", flush=True)
        records.append((model.name, workload, tally))

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, (volume, recall) = plt.subplots(1, 2, figsize=(11, 4), layout="constrained")
    groups = np.arange(len(transcripts))
    width = .75 / len(records)
    for index, (name, workload, _) in enumerate(records):
        volume.bar(groups + (index - (len(records) - 1) / 2) * width, workload, width, label=name)
    volume.set(xticks=groups, xticklabels=list(transcripts), title="All lines sent to review", ylabel="Flagged turns")
    volume.legend()
    names = [name for name, _, _ in records]
    caught = [counts["tp"] for _, _, counts in records]
    missed = [counts["fn"] for _, _, counts in records]
    recall.bar(names, caught, label="Caught", color="#166b9e")
    recall.bar(names, missed, bottom=caught, label="Missed", color="#b54c28")
    recall.set(title="Commitments among labelled turns", ylabel="Provisional commitments")
    recall.legend()
    figure.savefig(args.graph, dpi=170)
    plt.close(figure)


if __name__ == "__main__":
    main()
