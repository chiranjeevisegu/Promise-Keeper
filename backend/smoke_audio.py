"""Exercise the real model with a known spoken fixture via FastAPI."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from app.main import create_app


def main(recording: Path, expected: str):
    with TemporaryDirectory() as directory, TestClient(create_app(Path(directory) / "smoke.db")) as client:
        owner = client.post("/api/users", json={"name": "Test", "email": "test@example.com"})
        owner.raise_for_status()
        meeting = client.post("/api/meetings", json={
            "title": "Audio smoke test", "date": "2026-11-14", "owner_id": owner.json()["id"],
        })
        meeting.raise_for_status()
        url = f"/api/meetings/{meeting.json()['id']}/audio-transcript"
        with recording.open("rb") as file:
            result = client.post(url, files={"file": (recording.name, file)})
        if result.status_code != 201:
            raise RuntimeError(result.text)
        segments = result.json()["segments"]
        transcript = " ".join(segment["text"] for segment in segments)
        print(transcript)
        assert expected.casefold() in transcript.casefold(), transcript
        assert all(0 <= segment["start"] < segment["end"] for segment in segments)
        assert client.get(url).json() == result.json()


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2])
