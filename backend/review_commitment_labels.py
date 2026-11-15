"""Review the local annotation queue and append labels safely across sessions."""
import argparse
import json
from pathlib import Path

from app.commitment_model import DEFAULT_MODEL, LABELS


def main():
    folder = DEFAULT_MODEL.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, default=folder / "annotation-queue.jsonl")
    parser.add_argument("--output", type=Path, default=folder / "reviewed-labels.jsonl")
    args = parser.parse_args()
    output = args.output
    if args.queue.resolve() == output.resolve():
        parser.error("The queue and output must be different files.")
    seen = set()
    if output.exists():
        seen = {json.loads(line)["id"] for line in output.read_text(encoding="utf-8").splitlines() if line.strip()}
    print(" | ".join(f"{i}: {label}" for i, label in enumerate(LABELS, 1)))
    print("s: skip | x: exclude | q: quit")
    with args.queue.open(encoding="utf-8") as queue, output.open("a", encoding="utf-8") as saved:
        for line in queue:
            row = json.loads(line)
            if row["id"] in seen:
                continue
            timestamp = f" | {row['start']:.2f}–{row['end']:.2f} s" if "start" in row and "end" in row else ""
            print(f"\n{row['id']} | {row['split']}{timestamp}\nPrevious: {row['context']}\n{row['quote']}")
            if row.get("next_context"):
                print(f"Next: {row['next_context']}")
            if row.get("source_url"):
                print(row["source_url"])
            choice = input("Label: ").strip().lower()
            while choice not in {"1", "2", "3", "4", "5", "6", "q", "s", "x"}:
                choice = input("Use 1–6, s, x, or q: ").strip().lower()
            if choice == "q":
                break
            if choice != "s":
                row["label"] = None if choice == "x" else LABELS[int(choice) - 1]
                saved.write(json.dumps(row, ensure_ascii=False) + "\n")
                saved.flush()


if __name__ == "__main__":
    main()
