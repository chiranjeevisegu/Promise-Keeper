from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.auth import COOKIE
from app.commitment_model import LABELS
from app.main import create_app
from app.models import WorkspaceSession
from backup_workspace import backup_workspace


class PrivatePilotTest(TestCase):
    def test_private_api_origin_session_revocation_and_backup_restore(self):
        with TemporaryDirectory() as directory, patch.dict("os.environ", {
            "PROMISE_KEEPER_AUTH_REQUIRED": "1",
            "PROMISE_KEEPER_PASSWORD": "test-only-workspace-secret",
            "PROMISE_KEEPER_ORIGIN": "http://127.0.0.1:8000",
        }):
            path = Path(directory) / "pilot.db"
            copy = Path(directory) / "backup.db"
            origin = {"Origin": "http://127.0.0.1:8000"}
            with TestClient(create_app(path)) as client:
                self.assertEqual(client.get("/api/health").status_code, 200)
                self.assertEqual(client.get("/api/meetings").status_code, 401)
                self.assertEqual(client.get("/openapi.json").status_code, 404)
                self.assertEqual(client.get("/api/meetings").headers["Cache-Control"], "private, no-store")
                credentials = {"password": "test-only-workspace-secret"}
                self.assertEqual(client.post("/api/auth/login", json=credentials).status_code, 403)
                self.assertEqual(client.post("/api/auth/login", json={"password": "incorrect"}, headers=origin).status_code, 401)
                login = client.post("/api/auth/login", json=credentials, headers=origin).raise_for_status()
                self.assertIn("HttpOnly", login.headers["Set-Cookie"])
                self.assertIn("SameSite=strict", login.headers["Set-Cookie"])
                session = client.cookies[COOKIE]
                self.assertTrue(client.get("/api/auth/status").json()["authenticated"])
                user = {"name": "Priya", "email": "priya@example.com"}
                self.assertEqual(client.post("/api/users", json=user, headers={"Origin": "https://other.example"}).status_code, 403)
                client.post("/api/users", json=user, headers=origin).raise_for_status()
                backup_workspace(path, copy)
            with TestClient(create_app(path), cookies={COOKIE: session}) as restarted:
                self.assertEqual(len(restarted.get("/api/users").raise_for_status().json()), 1)
                restarted.post("/api/auth/logout", headers=origin).raise_for_status()
                restarted.cookies.set(COOKIE, session)
                self.assertEqual(restarted.get("/api/users").status_code, 401)
                restarted.cookies.clear()
                restarted.post("/api/auth/login", json=credentials, headers=origin).raise_for_status()
                with Session(restarted.app.state.engine) as db:
                    db.execute(update(WorkspaceSession).values(expires_at=0))
                    db.commit()
                self.assertEqual(restarted.get("/api/users").status_code, 401)
            with TestClient(create_app(copy), cookies={COOKIE: session}) as restored:
                self.assertEqual(restored.get("/api/users").status_code, 401)
                restored.post("/api/auth/login", json=credentials, headers=origin).raise_for_status()
                self.assertEqual(restored.get("/api/users").json()[0]["name"], "Priya")

    def test_required_mode_rejects_missing_password(self):
        with patch.dict("os.environ", {"PROMISE_KEEPER_AUTH_REQUIRED": "1", "PROMISE_KEEPER_PASSWORD": ""}):
            with self.assertRaisesRegex(RuntimeError, "workspace password"):
                with TestClient(create_app()):
                    pass


class ParallelMeetingsTest(TestCase):
    def test_second_meeting_can_finish_while_first_analysis_waits(self):
        with TemporaryDirectory() as directory, TestClient(create_app(Path(directory) / "pilot.db")) as client:
            actor = client.post("/api/users", json={"name": "Priya", "email": "priya@example.com"}).raise_for_status().json()["id"]

            def meeting(task):
                identifier = client.post("/api/meetings", json={
                    "title": task, "date": "2026-11-15", "owner_id": actor,
                }).raise_for_status().json()["id"]
                route = f"/api/meetings/{identifier}"
                client.put(route + "/transcript", json={"text": f"Priya: I'll send the {task}."}).raise_for_status()
                return route

            first, second = meeting("checklist"), meeting("report")
            running, release = Event(), Event()
            scores = dict.fromkeys(LABELS, 0.)
            scores["explicit_promise"] = 1.

            def predict(items):
                if "checklist" in items[0]["quote"]:
                    running.set()
                    if not release.wait(12):
                        raise RuntimeError("Timed out waiting for the parallel meeting.")
                return [scores] * len(items)

            def analyze(meeting_url):
                return client.post(meeting_url + "/commitments/manual?extract_details=false").raise_for_status().json()

            with patch("app.commitment_graph.predict", side_effect=predict), ThreadPoolExecutor(max_workers=2) as pool:
                pending = pool.submit(analyze, first)
                try:
                    self.assertTrue(running.wait(8))
                    other = pool.submit(analyze, second).result(timeout=8)
                    response = client.put(second + "/commitments/manual", json={
                        "revision": other["revision"], "approved": {"0": {"task": "Send the report", "owner": "Priya"}},
                    }).raise_for_status().json()
                    self.assertEqual(response["status"], "complete")
                finally:
                    release.set()
                initial = pending.result(timeout=8)
            client.put(first + "/transcript", json={"text": "Priya: I'll send the updated checklist."}).raise_for_status()
            self.assertEqual(client.put(first + "/commitments/manual", json={
                "revision": initial["revision"], "approved": {"0": {"task": "Send the checklist"}},
            }).status_code, 409)
            self.assertEqual([row["task"] for row in client.get("/api/promises").json()["items"]], ["Send the report"])
