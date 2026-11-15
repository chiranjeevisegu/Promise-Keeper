from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from fastapi.testclient import TestClient

from app.main import create_app


class MeetingWorkflowTest(TestCase):
    def test_restart_preserves_transcript_and_delete_removes_it(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "workspace.db"
            transcript = "Priya: I'll send the report by Friday.\nRahul: Thanks!"
            with TestClient(create_app(path)) as client:
                profile = {"name": "Priya", "email": "priya@example.com"}
                user = client.post("/api/users", json=profile)
                self.assertEqual(user.status_code, 201, user.text)
                self.assertEqual(client.post("/api/users", json=profile).status_code, 409)
                meeting = client.post("/api/meetings", json={
                    "title": "Weekly planning", "date": "2026-11-14", "owner_id": user.json()["id"],
                })
                self.assertEqual(meeting.status_code, 201, meeting.text)
                self.assertFalse(meeting.json()["has_transcript"])
                url = f"/api/meetings/{meeting.json()['id']}"
                save = client.put(url + "/transcript", json={"text": transcript})
                self.assertEqual(save.status_code, 200, save.text)

            with TestClient(create_app(path)) as client:
                self.assertTrue(client.get("/api/meetings").json()[0]["has_transcript"])
                self.assertEqual(client.get(url + "/transcript").json()["text"], transcript)
                updated = client.put(url + "/transcript", json={"text": "Edited transcript"})
                self.assertEqual(updated.status_code, 200, updated.text)
                self.assertEqual(client.get(url + "/transcript").json()["text"], "Edited transcript")
                self.assertEqual(client.delete(url).status_code, 204)
                self.assertEqual(client.get(url + "/transcript").status_code, 404)
                self.assertEqual(client.get("/api/meetings").json(), [])
