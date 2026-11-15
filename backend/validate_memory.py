"""Exercise live Phase 4 retrieval and citation selection on two local meeting recordings."""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import create_app
from app.models import AudioTranscript

CASES = [
    ("What breakfast did they propose to encourage attendance?", "pancake breakfast", "3WrZMzqpFTc"),
    ("What was suggested to curb the spread of illness in school?", "put up posters", "3WrZMzqpFTc"),
    ("What happened after the stair hazard tape was installed?", "tripped over the safety tape", "wAr_t2OsEdc"),
    ("Who will receive the new training timetable?", "communicate that out to managers", "wAr_t2OsEdc"),
    ("How much time was suggested for personal internet use?", "20 minutes per day", "wAr_t2OsEdc"),
    ("How much cheaper was the purchase option?", "15% cheaper", "wAr_t2OsEdc"),
    ("What did Rahul say about the API gateway?", "caches product results for thirty seconds", "manual"),
    ("Describe how the catalog service temporarily stores responses", "caches product results for thirty seconds", "manual"),
    ("What are the coordinates of Neptune's moons?", None, None),
    ("Which team won the underwater chess trophy?", None, None),
    ("What was approved for the cafeteria renovation budget?", None, None),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--videos", type=Path, default=Path(__file__).resolve().parent / "data/phase3/videos")
    args = parser.parse_args()
    with TemporaryDirectory() as directory, TestClient(create_app(Path(directory) / "memory.db")) as client:
        profile = client.post("/api/users", json={"name": "Rahul", "email": "rahul@example.com"}).raise_for_status().json()
        meetings = {}
        for index, title in enumerate(("3WrZMzqpFTc", "wAr_t2OsEdc", "manual"), start=1):
            meeting = client.post("/api/meetings", json={
                "title": title, "date": f"2026-11-{index:02d}", "owner_id": profile["id"],
            }).raise_for_status().json()
            meetings[title] = meeting["id"]
            if title == "manual":
                client.put(f"/api/meetings/{meeting['id']}/transcript", json={
                    "text": "Rahul: The API gateway caches product results for thirty seconds.\n"
                            "Priya: The team will discuss the office opening after lunch.",
                }).raise_for_status()
            else:
                recording = json.loads((args.videos / f"{title}.transcript.json").read_text(encoding="utf-8"))
                with Session(client.app.state.engine) as db:
                    db.add(AudioTranscript(meeting_id=meeting["id"], **recording))
                    db.commit()
        retrieved = answered = abstained = available = 0
        for question, fact, source in CASES:
            started = perf_counter()
            result = client.post("/api/memory/ask", json={"query": question}).raise_for_status().json()
            hits = {doc["id"]: doc for doc in result["matches"]}
            for claim in result["answer"]:
                assert claim["quote"] in hits[claim["source_id"]]["quote"], claim
            if fact:
                meeting_id = meetings[source]
                found = any(fact in row["quote"].casefold() and row["meeting_id"] == meeting_id for row in hits.values())
                cited = any(fact in claim["quote"].casefold() and hits[claim["source_id"]]["meeting_id"] == meeting_id
                            for claim in result["answer"])
                retrieved += found
                answered += cited
                outcome = f"evidence={found} answer={cited}"
                if not cited:
                    outcome += f" selections={[claim['quote'] for claim in result['answer']]}"
            else:
                refused = result["status"] == "insufficient"
                abstained += refused
                outcome = f"abstained={refused}"
            available += result["retrieval"] == "hybrid" and result["status"] != "generation_unavailable"
            print(f"{question} | {outcome} | {result['status']} | {perf_counter() - started:.2f}s", flush=True)
        positives = sum(fact is not None for _, fact, _ in CASES)
        negatives = len(CASES) - positives
        print(f"Evidence recall@8 {retrieved}/{positives} | Correct cited answers {answered}/{positives} | "
              f"Unknown-query abstention {abstained}/{negatives} | Hybrid and Ollama available {available}/{len(CASES)}")
        if (retrieved, answered, abstained, available) != (positives, positives, negatives, len(CASES)):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
