from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import wave

from fastapi.testclient import TestClient

from app.audio import decode, speaker_for
from app.main import create_app


def wav_file():
    buffer = BytesIO()
    with wave.open(buffer, "wb") as recording:
        recording.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        recording.writeframes(b"\x00\x00" * 8000)
    return buffer.getvalue()


class AudioWorkflowTest(TestCase):
    def test_decodes_and_resamples_wav(self):
        self.assertEqual(decode(BytesIO(wav_file()), ".wav").shape, (16000,))

    def test_speaker_assignment_uses_most_overlap(self):
        turns = [(0, 1, "SPEAKER_00"), (1, 4, "SPEAKER_01")]
        self.assertEqual(speaker_for(0.5, 2, turns), "SPEAKER_01")
        self.assertIsNone(speaker_for(5, 6, turns))

    def test_upload_edit_reopen_and_delete(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "workspace.db"
            with TestClient(create_app(path)) as client:
                owner = client.post("/api/users", json={"name": "Priya", "email": "priya@example.com"}).json()
                meeting = client.post("/api/meetings", json={
                    "title": "Planning", "date": "2026-11-14", "owner_id": owner["id"],
                }).json()
                meeting_url = f"/api/meetings/{meeting['id']}"
                url = meeting_url + "/audio-transcript"
                manual = client.put(meeting_url + "/transcript", json={"text": "Manual notes"})
                self.assertEqual(manual.status_code, 200)
                self.assertEqual(client.post(url, files={"file": ("clip.txt", b"text")}).status_code, 415)
                self.assertEqual(client.post(url, files={"file": ("broken.wav", b"bad audio")}).status_code, 422)
                segment = {"start": 0.25, "end": 1.0, "text": "Draft", "speaker": "SPEAKER_00"}
                with patch("app.audio.transcribe", return_value=[segment]) as transcribe:
                    response = client.post(url, files={"file": ("clip.wav", wav_file())}, data={"language": "en"})
                    self.assertEqual(transcribe.call_args.kwargs, {"language": "en"})
                self.assertEqual(response.status_code, 201, response.text)
                self.assertEqual(client.post(url, files={"file": ("clip.wav", wav_file())}).status_code, 409)
                changes = {"version": response.json()["version"], "texts": ["Edited"], "names": {"SPEAKER_00": "Priya"}}
                self.assertEqual(client.put(url, json=changes).status_code, 200)
                self.assertEqual(client.put(url, json=changes).status_code, 409)

            with TestClient(create_app(path)) as client:
                result = client.get(url).json()
                self.assertEqual(result["segments"], [dict(segment, text="Edited")])
                self.assertEqual(result["names"], {"SPEAKER_00": "Priya"})
                edit = {"version": result["version"], "texts": ["Edited"], "names": {}, "speakers": ["Rahul"]}
                self.assertEqual(client.put(url, json={**edit, "speakers": []}).status_code, 422)
                reassigned = client.put(url, json=edit).raise_for_status().json()
                self.assertEqual(reassigned["segments"], [dict(segment, text="Edited", speaker="Rahul")])
                self.assertEqual(client.get(meeting_url + "/transcript").json()["text"], "Manual notes")
                self.assertEqual(client.delete(meeting_url).status_code, 204)
                self.assertEqual(client.get(url).status_code, 404)
