from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.ledger import normalized
from app.main import create_app
from app.memory_routes import Passage
from app.models import Promise


class InsightWorkflowTest(TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.path = Path(directory) / "insights.db"
        self.client = self.enterContext(TestClient(create_app(self.path)))
        self.profile = self.client.post("/api/users", json={
            "name": "Priya", "email": "priya@example.com",
        }).raise_for_status().json()["id"]

    def promise(self, task, *, aged=2, owner="Priya"):
        meeting = self.client.post("/api/meetings", json={
            "title": f"Review {aged}", "date": (date.today() - timedelta(days=aged)).isoformat(),
            "owner_id": self.profile,
        }).raise_for_status().json()["id"]
        with Session(self.client.app.state.engine) as db:
            row = Promise(meeting_id=meeting, source="manual", evidence_key="0", line=0,
                          quote=f"I'll {task.lower()}.", speaker=owner, kind="manual",
                          owner=owner, owner_key=normalized(owner), task=task, task_key=normalized(task),
                          confirmed_at=date.today().isoformat())
            db.add(row)
            db.flush()
            identifier = row.id
            db.commit()
        return identifier

    def edit(self, identifier, *, status="pending", due=None, prerequisites=None, version=0, evidence=None):
        return self.client.put(f"/api/promises/{identifier}/lifecycle", json={
            "actor_id": self.profile, "version": version, "status": status, "due_date": due,
            "prerequisites": prerequisites or [], "confirm_completion": status == "completed", "evidence": evidence,
        }).raise_for_status().json()

    def test_risk_graph_and_history_respect_real_deadlines_and_dependencies(self):
        blocker = self.promise("Approve final designs", owner="Rahul")
        root = self.promise("Publish onboarding guide")
        follower = self.promise("Notify customers", aged=8)
        separate = self.promise("Publish onboarding guide", aged=12)
        self.edit(root, due=date.today().isoformat(), prerequisites=[blocker])
        self.edit(follower, prerequisites=[root])
        result = self.client.get("/api/insights", params={"owner": "priya", "days": 30}).raise_for_status().json()
        risks = {row["id"]: row for row in result["risks"]}
        self.assertEqual(risks[root]["level"], "high")
        self.assertTrue(any("Approve final designs" in reason for reason in risks[root]["reasons"]))
        self.assertEqual(risks[follower]["level"], "watch")
        self.assertEqual(result["metrics"]["total"], 3)
        self.assertEqual(result["recurring"][0]["meeting_count"], 2)
        self.assertTrue(any(row["score"] is None for row in risks.values()))
        self.assertEqual({row["id"] for row in result["graph_options"]}, {root, follower, separate})
        graph = self.client.get(f"/api/insights/dependencies/{root}").raise_for_status().json()
        self.assertEqual({(row["source"], row["target"]) for row in graph["edges"]},
                         {(blocker, root), (root, follower)})
        levels = {row["id"]: row["level"] for row in graph["nodes"]}
        self.assertLess(levels[blocker], levels[root])
        self.assertLess(levels[root], levels[follower])
        isolated = self.client.get(f"/api/insights/dependencies/{separate}").raise_for_status().json()
        self.assertEqual([node["id"] for node in isolated["nodes"]], [separate])
        self.assertEqual(isolated["edges"], [])

    def test_reviews_are_versioned_and_follow_current_completion_evidence(self):
        identifier = self.promise("Send the checklist")
        today = date.today().isoformat()
        past = (date.today() - timedelta(days=1)).isoformat()
        self.edit(identifier, due=today)
        self.edit(identifier, status="completed", due=today, version=1, evidence="Checklist receipt recorded.")
        url = f"/api/promises/{identifier}/evidence-review"
        detail = self.client.get(url).raise_for_status().json()
        self.assertEqual(detail["evidence"]["note"], "Checklist receipt recorded.")
        request = {"reviewer_id": self.profile, "event_id": detail["evidence"]["id"],
                   "revision": 0, "verdict": "supports", "note": "Checked the receipt."}
        saved = self.client.post(url, json=request).raise_for_status().json()
        self.assertEqual(saved["reviews"][0]["reviewer"], "Priya")
        self.assertEqual(self.client.post(url, json=request).status_code, 409)
        self.edit(identifier, status="completed", due=past, version=2)
        metrics = self.client.get("/api/insights").raise_for_status().json()["metrics"]
        self.assertEqual((metrics["on_time"], metrics["timed_completions"], metrics["deadline_changes"],
                          metrics["verified_completions"]), (1, 1, 1, 1))
        with TestClient(create_app(self.path)) as restarted:
            self.assertEqual(restarted.get(url).json()["reviews"][0]["note"], "Checked the receipt.")
        self.edit(identifier, status="unverified", due=past, version=3)
        self.assertIsNone(self.client.get(url).json()["evidence"])
        self.edit(identifier, status="completed", due=past, version=4, evidence="New delivery receipt.")
        self.assertEqual(self.client.post(url, json={**request, "revision": saved["reviews"][0]["id"]}).status_code, 409)
        self.assertEqual(self.client.get(url).json()["reviews"], [])

    def test_briefing_uses_only_known_candidates_and_falls_back_offline(self):
        urgent = self.promise("Finalize launch checklist")
        unknown = self.promise("Agree the next agenda")
        self.edit(urgent, due=(date.today() - timedelta(days=2)).isoformat())
        endpoint = "/api/insights/briefing"
        with patch("app.insight_routes.select_passages", side_effect=httpx.ReadTimeout("Offline")):
            briefing = self.client.post(endpoint, json={}).raise_for_status().json()
        self.assertEqual(briefing["mode"], "rules")
        self.assertEqual(briefing["counts"], {"open": 2, "high_risk": 1, "overdue": 1, "due_today": 0, "undated": 1})

        def selections(_question, sources):
            reference = next(source for source in sources if source.id == str(unknown))
            return [Passage(source_id="invented", quote="Invented source."),
                    Passage(source_id=reference.id, quote=reference.quote)]

        with patch("app.insight_routes.select_passages", side_effect=selections):
            briefing = self.client.post(endpoint, json={}).raise_for_status().json()
        self.assertEqual(briefing["mode"], "ai")
        self.assertEqual([row["id"] for row in briefing["items"]], [urgent, unknown])
