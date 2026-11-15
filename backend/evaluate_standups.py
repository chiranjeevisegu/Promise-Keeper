"""Measure a frozen classifier against timestamp-aligned, user-supplied reference events."""
import argparse
import json
from pathlib import Path

from app.audio import transcribe
from app.commitment_graph import extract
from app.commitment_model import commitment_score, configured_model, predict, routes_to_review, trained_review_threshold

ROOT = Path(__file__).resolve().parent
RECORDINGS = ("T8GcCl8w-_E", "MsxcpZr1LpM")


def prepare(folder, recording_id, language=None):
    suffix = f".{language}" if language else ""
    output = folder / f"{recording_id}{suffix}.transcript.json"
    if output.is_file():
        return json.loads(output.read_text(encoding="utf-8"))
    matches = sorted((folder / "recordings").glob(f"{recording_id}.*"))
    audio = next((file for file in matches if file.suffix in {".mp4", ".m4a", ".mp3", ".wav"}), None)
    if audio is None:
        raise FileNotFoundError(f"No recording for {recording_id} under {folder / 'recordings'}")
    print(f"Transcribing {audio.name}", flush=True)
    with audio.open("rb") as source:
        rows = transcribe(source, audio.suffix, diarize=False, language=language)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return rows


def segments_as_items(rows):
    return [{
        "quote": row["text"],
        "speaker": row.get("speaker"),
        "start": row["start"],
        "context": f"{rows[index - 1].get('speaker') or 'Unknown speaker'}: {rows[index - 1]['text']}" if index else "",
        "next_context": f"{rows[index + 1].get('speaker') or 'Unknown speaker'}: {rows[index + 1]['text']}"
        if index + 1 < len(rows) else "",
    } for index, row in enumerate(rows)]


def build_review_queue(folder, output):
    rows = []
    for recording_id, language in zip(RECORDINGS, ("en", None), strict=True):
        segments = prepare(folder, recording_id, language=language)
        if not segments:
            raise ValueError(f"No transcript segments for {recording_id}.")
        for index, (item, segment) in enumerate(zip(segments_as_items(segments), segments, strict=True)):
            rows.append({
                **item, "id": f"youtube:{recording_id}:{index + 1}", "source": "youtube",
                "group": recording_id, "sample": recording_id, "split": "test",
                "end": segment["end"], "label": None,
                "source_url": f"https://www.youtube.com/watch?v={recording_id}&t={int(item['start'])}s",
            })
    with output.open("x", encoding="utf-8") as queue:
        for row in rows:
            queue.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"Queued {len(rows)} transcript segments for blind review in {output}.")


def evaluate(rows, references, *, with_extraction=False):
    items = segments_as_items(rows)
    probabilities = predict(items)
    flags = [routes_to_review(max(scores, key=scores.__getitem__), scores) for scores in probabilities]
    covered = missed = 0
    report = []
    for reference in references:
        result = dict(reference)
        if reference["decision"] == "commitment":
            anchors = [i for i, item in enumerate(items) if any(abs(item["start"] - t) <= .15 for t in reference["starts"])]
            if len(anchors) != len(reference["starts"]) or not anchors:
                raise ValueError(f"Missing or ambiguous timestamp anchor for {reference['event']}.")
            hit = any(flags[index] for index in anchors)
            covered += hit
            missed += not hit
            result["routed"] = hit
            result["anchors"] = [{
                "start": items[index]["start"], "score": round(commitment_score(probabilities[index]), 4),
                "routed": flags[index],
            } for index in anchors]
            if with_extraction and hit:
                index = max((index for index in anchors if flags[index]), key=lambda i: commitment_score(probabilities[i]))
                extracted = extract({"candidates": [items[index]]})
                result["extraction_unavailable"] = extracted["extraction_unavailable"]
                result["suggested_fields"] = extracted["candidates"][0].get("details")
        report.append(result)
    return {"segments": len(items), "sent_to_review": sum(flags), "caught": covered, "missed": missed, "events": report}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, default=ROOT / "data/phase8")
    parser.add_argument("--review", type=Path, default=ROOT / "evaluation/phase8-standups.json")
    parser.add_argument("--output", type=Path, help="Optional output file. Defaults to standup-evaluation.json in the recording folder.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--prepare-only", action="store_true")
    modes.add_argument("--review-queue", type=Path, help="Create an unlabeled review queue using the English override.")
    parser.add_argument("--english-first", action="store_true", help="Use the English override for the first recording.")
    parser.add_argument("--extract", action="store_true", help="Save Ollama draft fields for manual field review.")
    args = parser.parse_args()
    if args.review_queue:
        if args.extract or args.output:
            parser.error("--review-queue writes an unlabeled file and cannot be combined with --extract or --output.")
        try:
            build_review_queue(args.folder, args.review_queue)
        except FileExistsError:
            parser.error(f"Review queue already exists: {args.review_queue}")
        return
    references = {} if args.prepare_only else json.loads(args.review.read_text(encoding="utf-8"))["recordings"]
    totals = []
    if not args.prepare_only:
        import torch
        torch.set_num_threads(min(4, torch.get_num_threads()))
    for recording_id in RECORDINGS:
        english = args.english_first and recording_id == RECORDINGS[0]
        rows = prepare(args.folder, recording_id, language="en" if english else None)
        print(f"{recording_id}: {len(rows)} timestamped transcript segments", flush=True)
        if args.prepare_only:
            continue
        key = "english_references" if english else "references"
        report = evaluate(rows, references[recording_id][key], with_extraction=args.extract)
        report["source"] = references[recording_id]["url"]
        report["transcription"] = "English override" if english else "Auto detect"
        report["model"] = configured_model().name
        report["review_threshold"] = trained_review_threshold(str(configured_model()))
        totals.append(report)
        print(f"  Routed {report['sent_to_review']} segments. Caught {report['caught']} and missed {report['missed']} aligned commitments.", flush=True)
        for result in report["events"]:
            outcome = str(result["routed"]) if "routed" in result else result["decision"]
            print(f"  {result['event']} | {outcome} | {result['starts']}", flush=True)
    if totals:
        report_path = args.output or args.folder / "standup-evaluation.json"
        report_path.write_text(json.dumps(totals, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("User-supplied reference events are not an exhaustive segment annotation. Precision is unmeasured.")


if __name__ == "__main__":
    main()
