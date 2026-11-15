import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.commitment_model import LABELS
from app.main import create_app
from app.memory_routes import Passage


class MemoryWorkflowTest(TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.path = Path(directory) / "test.db"
        self.client = self.enterContext(TestClient(create_app(self.path)))
        self.profile = self.client.post("/api/users", json={
            "name": "Priya", "email": "priya@example.com",
        }).raise_for_status().json()["id"]

    def meeting(self, text, day="2026-11-10"):
        response = self.client.post("/api/meetings", json={
            "title": "Planning", "date": day, "owner_id": self.profile,
        }).raise_for_status()
        base = f"/api/meetings/{response.json()['id']}"
        self.client.put(base + "/transcript", json={"text": text}).raise_for_status()
        return base

    def review(self, meeting):
        scores = dict.fromkeys(LABELS, 0.)
        scores["non_commitment"] = 1.
        with patch("app.commitment_graph.predict", side_effect=lambda items: [scores] * len(items)):
            return self.client.post(meeting + "/commitments/manual?extract_details=false").raise_for_status().json()

    def approve(self, meeting, review, line=0):
        body = {"revision": review["revision"], "approved": {str(line): {
            "task": "Send the launch checklist", "owner": "Priya", "recipient": "Rahul", "deadline": "Friday",
        }}}
        self.client.put(meeting + "/commitments/manual", json=body).raise_for_status()
        self.assertEqual(self.client.put(meeting + "/commitments/manual", json=body).status_code, 409)

    def test_ledger_only_collects_confirmed_evidence_and_filters_history(self):
        promise = "Priya: I'll send Rahul the launch checklist by Friday."
        first = self.meeting(promise)
        review = self.review(first)
        self.assertEqual(self.client.get("/api/promises").json()["total"], 0)
        self.approve(first, review)

        second = self.meeting(promise, "2026-11-18")
        self.approve(second, self.review(second))
        page = self.client.get("/api/promises", params={
            "owner": "PRIYA", "deadline": "friday", "status": "pending", "limit": 1,
        }).raise_for_status().json()
        self.assertEqual(page["total"], 2)
        item, = page["items"]
        self.assertEqual(item["quote"], promise.split(": ", 1)[1])
        self.assertEqual((item["recipient"], item["source"], item["start"]), ("Rahul", "manual", None))
        self.assertEqual(self.client.get("/api/promises", params={"status": "completed"}).json()["total"], 0)

        pending = self.client.post("/api/memory/ask", json={"query": "Which commitments are still pending?"}).json()
        self.assertEqual((pending["status"], pending["total"]), ("ledger", 2))
        body = {"query": "What did I promise last week?", "as_of": "2026-11-18"}
        self.assertEqual(self.client.post("/api/memory/ask", json=body).status_code, 422)
        previous = self.client.post("/api/memory/ask", json={**body, "profile_id": self.profile}).raise_for_status().json()
        self.assertEqual((previous["status"], previous["total"]), ("ledger", 1))
        self.assertEqual(previous["matches"][0]["meeting_date"], "2026-11-10")

        related = self.client.get(f"/api/promises/{item['id']}/related").raise_for_status().json()
        self.assertEqual(len(related["duplicates"]), 1)
        self.assertNotEqual(related["duplicates"][0]["meeting_id"], item["meeting_id"])

    def test_retry_reconfirmation_and_legacy_backfill_are_idempotent(self):
        promise = "Priya: I'll send Rahul the launch checklist by Friday."
        meeting = self.meeting(promise)
        self.approve(meeting, self.review(meeting))
        self.client.put(meeting + "/transcript", json={"text": "Rahul: Hello.\n" + promise}).raise_for_status()
        self.approve(meeting, self.review(meeting), line=1)
        self.assertEqual(self.client.get("/api/promises").json()["total"], 1)

        # Simulate a completed Phase 3 review that predates the ledger tables.
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("DELETE FROM promises")
            connection.execute("DELETE FROM ledger_reviews")
        with TestClient(create_app(self.path)) as restarted:
            page = restarted.get("/api/promises").raise_for_status().json()
            self.assertEqual(page["total"], 1)
            self.assertEqual(page["items"][0]["line"], 1)
            self.assertEqual(restarted.get("/api/promises").json()["total"], 1)

    def test_audio_promise_retains_timestamp_and_is_removed_with_its_source(self):
        meeting = self.meeting("")
        segment = {"text": "I'll send the launch checklist.", "speaker": "SPEAKER_00", "start": 17.5, "end": 20.0}
        with patch("app.audio.transcribe", return_value=[segment]):
            self.client.post(meeting + "/audio-transcript", files={
                "file": ("clip.wav", b"audio", "audio/wav"),
            }).raise_for_status()
        scores = dict.fromkeys(LABELS, 0.)
        scores["non_commitment"] = 1.
        url = meeting + "/commitments/audio?extract_details=false"
        with patch("app.commitment_graph.predict", return_value=[scores]):
            review = self.client.post(url).raise_for_status().json()
        self.client.put(url, json={"revision": review["revision"], "approved": {"0": {
            "owner": "Priya", "task": "Send the launch checklist",
        }}}).raise_for_status()
        record, = self.client.get("/api/promises").raise_for_status().json()["items"]
        self.assertEqual((record["start"], record["source"], record["quote"]), (17.5, "audio", segment["text"]))
        self.client.delete(meeting + "/audio-transcript").raise_for_status()
        self.assertEqual(self.client.get("/api/promises").raise_for_status().json()["total"], 0)

    def test_answer_only_accepts_exact_passages_and_respects_source_changes(self):
        meeting = self.meeting("Rahul: The API gateway caches product results for thirty seconds.")
        self.meeting("Priya: The office furniture delivery is delayed.")
        query = {"query": "What did Rahul say about the API gateway?"}
        with patch("app.memory.semantic_scores", side_effect=OSError):
            results = self.client.post("/api/memory/search", json=query).raise_for_status().json()
            self.assertEqual((results["retrieval"], len(results["matches"])), ("keyword", 1))
            source, = results["matches"]
            self.assertEqual(source["speaker"], "Rahul")
            cited = Passage(source_id=source["id"], quote="caches product results for thirty seconds")
            with patch("app.memory_routes.select_passages", return_value=[
                cited,
                Passage(source_id=source["id"], quote="caches product results for one hour"),
                Passage(source_id="made-up", quote=cited.quote),
            ]):
                answer = self.client.post("/api/memory/ask", json=query).raise_for_status().json()
                self.assertEqual(answer["status"], "grounded")
                self.assertEqual(answer["answer"], [cited.model_dump()])

            self.client.put(meeting + "/transcript", json={"text": "Rahul: Lunch is at noon."}).raise_for_status()
            self.assertEqual(self.client.post("/api/memory/search", json={"query": "API gateway"}).json()["matches"], [])
            self.client.delete(meeting).raise_for_status()
            self.assertEqual(self.client.post("/api/memory/search", json={"query": "noon"}).json()["matches"], [])
