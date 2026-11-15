"""Sample human-authored source rows into a blank annotation queue."""
import json
import random
import re
from collections import Counter, defaultdict

import pyarrow.parquet as pq

from app.commitment_model import DEFAULT_MODEL

ROOT = DEFAULT_MODEL.parent
CUE = re.compile(r"\b(?:promise|guarantee|commit|i'll|we'll|will|plan to|going to|intend|should|could you|can you)\b", re.I)


def main():
    rng = random.Random(42)
    buckets = defaultdict(list)
    for folder in ("train", "validation", "test"):
        files = sorted(ROOT.glob(f"ami-{folder}*.parquet"))
        if not files:
            raise SystemExit(f"Missing AMI {folder} parquet in {ROOT}")
        rows = [row for file in files for row in pq.read_table(file).to_pylist() if row["source"] == "ami"]
        previous = {}
        for row in sorted(rows, key=lambda item: (item["note_id"], item["sentence_id"])):
            meeting = row["note_id"]
            quote = row["sentence_text"].strip()
            context = previous.get(meeting, "")
            previous[meeting] = f"{row['speaker']}: {quote}"
            if len(quote.split()) >= 3:
                bucket = "action" if row["zone"] == "action_item" else "cue" if CUE.search(quote) else "other"
                buckets["ami", folder, bucket].append(dict(
                    id=f"ami:{meeting}:{row['sentence_id']}", source="ami", split=folder,
                    group=meeting[:-1] if meeting[-1:].islower() else meeting,
                    quote=quote, context=context, source_label=row["zone"], label=None,
                ))
    for filename, split in [("Ask0729-fixed.txt", "train"), ("testSet-qualifiedBatch-fixed.txt", "test")]:
        for index, line in enumerate((ROOT / filename).read_text(encoding="utf-8-sig", errors="replace").splitlines()):
            label, sep, quote = line.partition("\t")
            quote = quote.strip()
            if sep and quote:
                buckets["enron", split, label].append(dict(
                    id=f"enron:{filename}:{index}", source="enron", split=split, group=filename,
                    quote=quote, context="", source_label=label, label=None,
                ))
    selected = []
    quotes = set()
    for (source, split, _), rows in sorted(buckets.items()):
        count = {"train": 200, "validation": 40, "test": 40}[split] if source == "ami" else {"train": 110, "test": 20}[split]
        sampled = 0
        rng.shuffle(rows)
        for row in rows:
            quote = " ".join(row["quote"].casefold().split())
            if quote in quotes:
                continue
            quotes.add(quote)
            selected.append(row)
            sampled += 1
            if sampled == count:
                break
    output = ROOT / "annotation-queue.jsonl"
    with output.open("x", encoding="utf-8") as file:
        for item in selected:
            file.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(Counter((row["source"], row["split"]) for row in selected))


if __name__ == "__main__":
    main()
