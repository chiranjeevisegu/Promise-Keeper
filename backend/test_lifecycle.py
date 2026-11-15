import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.commitment_model import LABELS
from app.main import create_app


class PromiseLifecycleTest(TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.path = Path(directory) / "lifecycle.db"
        self.client = self.enterContext(TestClient(create_app(self.path)))
        self.actor = self.client.post("/api/users", json={
            "name": "Priya", "email": "priya@example.com",
        }).raise_for_status().json()["id"]

    def meeting(self, title, transcript, day="2026-11-10"):
        meeting = self.client.post("/api/meetings", json={
            "title": title, "date": day, "owner_id": self.actor,
        }).raise_for_status().json()
        base = f"/api/meetings/{meeting['id']}"
        self.client.put(base + "/transcript", json={"text": transcript}).raise_for_status()
        return base

    def promise(self, task, deadline="Friday"):
        meeting = self.meeting(task, f"Priya: I'll {task.lower()} by Friday.")
        scores = dict.fromkeys(LABELS, 0.)
        scores["non_commitment"] = 1.
        route = meeting + "/commitments/manual?extract_details=false"
        with patch("app.commitment_graph.predict", return_value=[scores]):
            review = self.client.post(route).raise_for_status().json()
        self.client.put(route, json={"revision": review["revision"], "approved": {"0": {
            "task": task, "owner": "Priya", "deadline": deadline,
        }}}).raise_for_status()
        promise, = self.client.get("/api/promises", params={"meeting_id": int(meeting.split("/")[-1])}).json()["items"]
        return promise, meeting

    def edit(self, promise, **changes):
        return self.client.put(f"/api/promises/{promise['id']}/lifecycle", json={
            "actor_id": self.actor, "version": promise["lifecycle_version"],
            "status": promise["workflow_status"], "due_date": promise["due_date"], "prerequisites": [], **changes,
        })

    def test_deadlines_need_review_and_are_reflected_in_ledger_and_memory(self):
        promise, _ = self.promise("Send launch checklist")
        self.assertEqual((promise["due_date"], promise["suggested_due_date"], promise["status"]), (None, "2026-11-13", "pending"))
        self.meeting("Follow-up", "Priya: We moved the launch checklist from Friday to next Monday.", "2026-11-12")
        self.meeting("Different project", "Priya: We moved the onboarding video to Thursday.", "2026-11-12")
        self.meeting("Rejected proposal", "Priya: We will not move the launch checklist to Tuesday.", "2026-11-12")
        details = self.client.get(f"/api/promises/{promise['id']}/lifecycle").raise_for_status().json()
        suggestion, = details["deadline_suggestions"]
        self.assertEqual(suggestion["date"], "2026-11-16")
        self.assertIn("next Monday", suggestion["quote"])
        self.assertEqual(details["history"], [])
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        result = self.edit(promise, status="in_progress", due_date=yesterday).raise_for_status().json()
        self.assertEqual(result["promise"]["status"], "overdue")
        event, = result["history"]
        self.assertEqual(event["actor"], "Priya")
        self.assertEqual(event["changes"]["due_date"], {"from": None, "to": yesterday})
        self.assertEqual(self.edit(promise, status="cancelled").status_code, 409)
        self.assertEqual(self.client.get("/api/promises", params={"status": "overdue", "deadline": yesterday}).json()["total"], 1)
        memory = self.client.post("/api/memory/ask", json={"query": "Which commitments are overdue?"}).raise_for_status().json()
        self.assertEqual((memory["status"], memory["matches"][0]["due_date"]), ("ledger", yesterday))
        today = date.today().isoformat()
        current = self.edit(result["promise"], due_date=today).raise_for_status().json()
        self.assertEqual(current["promise"]["status"], "in_progress")
        self.assertEqual(current["history"][0]["changes"]["due_date"], {"from": yesterday, "to": today})

    def test_dependency_cycles_and_unverified_completion_require_review(self):
        checklist, _ = self.promise("Send launch checklist")
        approvals, _ = self.promise("Collect launch approvals")
        options = self.client.get(f"/api/promises/{checklist['id']}/dependency-options?q=approvals").json()
        self.assertEqual([item["id"] for item in options], [approvals["id"]])
        result = self.edit(checklist, status="unverified", prerequisites=[approvals["id"]]).raise_for_status().json()
        pending = result["promise"]
        self.assertEqual(result["prerequisites"][0]["id"], approvals["id"])
        self.assertEqual(self.edit(approvals, prerequisites=[checklist["id"]]).status_code, 422)
        self.assertEqual(self.edit(pending, status="completed", prerequisites=[approvals["id"]],
                                   confirm_completion=True, evidence="Sent.").status_code, 409)
        self.assertEqual(self.edit(approvals, status="completed", evidence="Approvals received").status_code, 422)
        self.assertEqual(self.edit(approvals, status="completed", confirm_completion=True).status_code, 422)
        complete = self.edit(approvals, status="completed", confirm_completion=True,
                             evidence="Approvals received").raise_for_status().json()["promise"]
        result = self.edit(pending, status="completed", prerequisites=[approvals["id"]], confirm_completion=True,
                           evidence="Checklist sent", evidence_url="https://example.com/checklist").raise_for_status().json()
        self.assertEqual(result["promise"]["status"], "completed")
        self.assertEqual(result["history"][0]["evidence"], "Checklist sent")
        self.assertEqual(self.edit(complete, status="pending").status_code, 409)
        with TestClient(create_app(self.path)) as restarted:
            recovered = restarted.get(f"/api/promises/{checklist['id']}/lifecycle").raise_for_status().json()
            self.assertEqual(recovered["history"][0]["actor"], "Priya")
            self.assertEqual(recovered["history"][0]["evidence_url"], "https://example.com/checklist")
            self.assertEqual(recovered["prerequisites"][0]["status"], "completed")

    def test_upgrade_preserves_phase_four_rows(self):
        promise, _ = self.promise("Send launch checklist")
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("ALTER TABLE promises DROP COLUMN due_date")
            connection.execute("ALTER TABLE promises DROP COLUMN lifecycle_version")
        for _ in range(2):
            with TestClient(create_app(self.path)) as restarted:
                item, = restarted.get("/api/promises").raise_for_status().json()["items"]
                self.assertEqual(item["id"], promise["id"])
                self.assertEqual(item["quote"], promise["quote"])
                self.assertEqual((item["due_date"], item["lifecycle_version"]), (None, 0))
