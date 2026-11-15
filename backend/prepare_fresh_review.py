"""Build a blind review queue from public transcripts outside the existing dataset."""
import json
import random
import re
from zipfile import ZipFile

from app.commitment_model import DEFAULT_MODEL

ROOT = DEFAULT_MODEL.parent
SOURCE = "https://github.com/NathanDuran/MRDA-Corpus"
TURNS = re.compile(r"/mrda_data/test/([^/]+)\.txt$")
CUE = re.compile(
    r"\b(?:i['’]ll|we['’]ll|i will|we will|i plan to|i intend to|"
    r"i(?:'m| am) going to|we should|can you|could you|would you)\b", re.I
)
HANSARD = (
    ("Commons/2021-12-10", "I promise to write to my hon. Friend on the specific issues that he raised and I will look into them very seriously."),
    ("Lords/2023-03-08", "I promise to write to the noble Lord, Lord Collins."),
    ("Commons/2025-01-28/88da8573-52ac-4837-a821-63f6eae1ae04", "I hope to answer all of them and, if I do not, I promise to write to him with clarification."),
    (
        "Lords/2026-01-08/debates/0e896ec1-1ba5-408c-8251-c629c065652f/LordsChamber",
        "I promise to go away and come back on Report with a better version of them",
    ),
    (
        "Commons/2013-07-05/debates/e739918a-ca12-4a4f-aa72-004efb8bfb4e/CommonsChamber",
        "I promise the House that I have been directly misquoted.",
    ),
)


def normalize(text):
    return re.sub(r"[^\w]+", " ", text.casefold()).strip()


def past_quotes():
    seen = set()
    for name in ("annotation-queue.jsonl", "diagnostic-labels.jsonl", "balanced-training.jsonl"):
        path = ROOT / name
        if path.exists():
            seen.update(normalize(json.loads(line)["quote"]) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip())
    return seen


def meeting_candidates(archive, min_words=3):
    for path in sorted(archive.namelist()):
        match = TURNS.search(path)
        if not match:
            continue
        meeting = match[1]
        turns = [line.split("|") for line in archive.read(path).decode("utf-8").splitlines() if line.strip()]
        for index, (speaker, quote, *_) in enumerate(turns):
            if len(quote.split()) < min_words:
                continue
            before = turns[index - 1] if index else None
            after = turns[index + 1] if index + 1 < len(turns) else None
            yield {
                "id": f"icsi:{meeting}:{index + 1}", "source": "icsi", "split": "test",
                "group": meeting, "speaker": speaker, "quote": quote,
                "context": f"{before[0]}: {before[1]}" if before else "",
                "next_context": f"{after[0]}: {after[1]}" if after else "",
                "source_url": f"{SOURCE}/blob/master/mrda_data/test/{meeting}.txt#L{index + 1}",
                "label": None,
            }


def main():
    seen = past_quotes()
    rng = random.Random(65)
    with ZipFile(ROOT / "mrda.zip") as archive:
        candidates = list(meeting_candidates(archive))
    rng.shuffle(candidates)
    selected = []
    for index, (path, quote) in enumerate(HANSARD, start=1):
        endpoint = "" if "/debates/" in path else "pdf/"
        selected.append(dict(
            id=f"hansard:{index}", source="hansard", split="test", group=path,
            quote=quote, context=(
                "The speaker imagines what another member could say about amendments." if index == 4 else ""
            ), label=None,
            source_url=f"https://hansard.parliament.uk/{endpoint}{path}", sample="challenge",
        ))
        seen.add(normalize(quote))
    for sample in ("random", "challenge"):
        count = 0
        for row in candidates:
            key = normalize(row["quote"])
            if key in seen or (sample == "challenge" and not CUE.search(row["quote"])):
                continue
            seen.add(key)
            selected.append({**row, "sample": sample})
            count += 1
            if count == 60:
                break
        if count < 60:
            raise ValueError(f"Only {count} ICSI rows available for {sample} review.")
    rng.shuffle(selected)
    with (ROOT / "fresh-test-queue.jsonl").open("x", encoding="utf-8") as output:
        for row in selected:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"Queued {len(selected)} unlabeled examples for independent review.")


if __name__ == "__main__":
    main()
