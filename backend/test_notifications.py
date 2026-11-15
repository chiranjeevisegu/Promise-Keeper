from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.commitment_model import LABELS
from app.main import create_app
from app.notifications import start_scheduler


class NotificationWorkflowTest(TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.path = Path(directory) / "notifications.db"

    def client(self, *, automatic=False):
        app = create_app(self.path)
        scheduler = (lambda engine: start_scheduler(engine, interval_seconds=.2)) if automatic else None
        with patch("app.main.start_scheduler", side_effect=scheduler):
            return self.enterContext(TestClient(app))

    def user(self, client, name, email):
        return client.post("/api/users", json={"name": name, "email": email}).raise_for_status().json()["id"]

    def promise(self, client, profile, task, *, owner="Priya", due=None, status="pending"):
        meeting = client.post("/api/meetings", json={
            "title": f"Planning for {task}", "date": "2026-11-12", "owner_id": profile,
        }).raise_for_status().json()["id"]
        base = f"/api/meetings/{meeting}"
        client.put(base + "/transcript", json={"text": f"{owner}: I'll {task.lower()} by Friday."}).raise_for_status()
        scores = dict.fromkeys(LABELS, 0.)
        scores["non_commitment"] = 1.
        with patch("app.commitment_graph.predict", return_value=[scores]):
            review = client.post(base + "/commitments/manual?extract_details=false").raise_for_status().json()
        client.put(base + "/commitments/manual", json={"revision": review["revision"], "approved": {"0": {
            "task": task, "owner": owner, "deadline": "Friday",
        }}}).raise_for_status()
        record, = client.get("/api/promises", params={"meeting_id": meeting}).json()["items"]
        lifecycle = f"/api/promises/{record['id']}/lifecycle"
        client.put(lifecycle, json={
            "actor_id": profile, "status": status, "version": 0, "due_date": due,
            "prerequisites": [], "confirm_completion": status == "completed",
            "evidence": "Reviewed and delivered" if status == "completed" else None,
        }).raise_for_status()
        return record["id"]

    def test_rules_follow_profile_timezone_and_skip_ineligible_promises(self):
        client = self.client()
        priya = self.user(client, "Priya", "priya@example.com")
        rahul = self.user(client, "Rahul", "rahul@example.com")
        root = f"/api/profiles/{priya}"
        preferences = root + "/notification-preferences"
        self.assertFalse(client.get(preferences).json()["settings"]["enabled"])
        self.assertEqual(client.put(preferences, json={"timezone": "imaginary/zone"}).status_code, 422)
        client.put(preferences, json={
            "enabled": True, "timezone": "Asia/Kolkata", "lead_days": 2, "summary_time": "00:25",
        }).raise_for_status()

        past = self.promise(client, priya, "Send pilot report", due="2026-11-15")
        today = self.promise(client, priya, "Send agenda", due="2026-11-16")
        soon = self.promise(client, priya, "Send budget", due="2026-11-18")
        self.promise(client, priya, "Refine estimates", due="2026-11-26")
        self.promise(client, priya, "Review survey")
        self.promise(client, priya, "Finish demo", due="2026-11-16", status="completed")
        self.promise(client, priya, "Plan launch", due="2026-11-16", status="cancelled")
        self.promise(client, priya, "Prepare assets", owner="Rahul", due="2026-11-16")
        fixed = datetime(2026, 11, 15, 19, tzinfo=timezone.utc)
        with patch("app.notifications.utc_now", return_value=fixed):
            self.assertEqual(client.post(root + "/notifications/check").json()["created"], 4)
            self.assertEqual(client.post(root + "/notifications/check").json()["created"], 0)
        inbox = client.get(root + "/notifications").raise_for_status().json()
        self.assertEqual(inbox["unread"], 4)
        self.assertEqual({(item["kind"], item["promise_id"]) for item in inbox["items"]},
                         {("upcoming", today), ("upcoming", soon), ("overdue", past), ("summary", None)})
        summary = next(item for item in inbox["items"] if item["kind"] == "summary")
        self.assertIn("5 open · 1 overdue · 1 due today · 1 due soon · 1 due later · 1 without a date", summary["body"])
        item = next(item for item in inbox["items"] if item["promise_id"] == past)
        mark = {"notification_id": item["id"]}
        self.assertEqual(client.put(f"/api/profiles/{rahul}/notifications/read", params=mark).status_code, 404)
        client.put(root + "/notifications/read", params=mark).raise_for_status()
        self.assertEqual(client.get(root + "/notifications").json()["unread"], 3)
        draft = client.get(f"/api/promises/{past}/follow-up").raise_for_status().json()["draft"]
        self.assertIn("Hi Priya", draft)
        self.assertIn("Send pilot report", draft)
        self.assertEqual(client.get("/api/promises", params={"promise_id": past}).json()["total"], 1)

        with patch("app.main.start_scheduler"), TestClient(create_app(self.path)) as restarted:
            self.assertEqual(restarted.get(root + "/notifications").json()["unread"], 3)
            with patch("app.notifications.utc_now", return_value=fixed):
                self.assertEqual(restarted.post(root + "/notifications/check").json()["created"], 0)

    def test_rescheduling_and_preferences_change_notification_eligibility(self):
        client = self.client()
        profile = self.user(client, "Priya", "priya@example.com")
        root = f"/api/profiles/{profile}"
        record = self.promise(client, profile, "Send launch checklist", due="2026-11-16")
        client.put(root + "/notification-preferences", json={
            "enabled": True, "daily_summary": False, "timezone": "UTC",
        }).raise_for_status()
        fixed = datetime(2026, 11, 16, 11, tzinfo=timezone.utc)
        with patch("app.notifications.utc_now", return_value=fixed):
            self.assertEqual(client.post(root + "/notifications/check").json()["created"], 1)
            lifecycle = f"/api/promises/{record}/lifecycle"
            client.put(lifecycle, json={"actor_id": profile, "status": "pending", "version": 1,
                                       "due_date": "2026-11-17", "prerequisites": []}).raise_for_status()
            self.assertEqual(client.post(root + "/notifications/check").json()["created"], 1)
            client.put(lifecycle, json={"actor_id": profile, "status": "cancelled", "version": 2,
                                       "due_date": "2026-11-17", "prerequisites": []}).raise_for_status()
        with patch("app.notifications.utc_now", return_value=fixed + timedelta(days=4)):
            self.assertEqual(client.post(root + "/notifications/check").json()["created"], 0)
        self.assertEqual(client.get(f"/api/promises/{record}/follow-up").status_code, 409)
        self.assertTrue(all(not item["can_follow_up"] for item in client.get(root + "/notifications").json()["items"]))
        client.put(root + "/notifications/read").raise_for_status()
        self.assertEqual(client.get(root + "/notifications").json()["unread"], 0)

    def test_background_scheduler_populates_inbox_without_manual_check(self):
        client = self.client(automatic=True)
        profile = self.user(client, "Priya", "priya@example.com")
        due = datetime.now(timezone.utc).date().isoformat()
        self.promise(client, profile, "Send scheduled report", due=due)
        root = f"/api/profiles/{profile}"
        client.put(root + "/notification-preferences", json={
            "enabled": True, "daily_summary": False, "timezone": "UTC",
        }).raise_for_status()
        until = monotonic() + 5
        while monotonic() < until:
            if client.get(root + "/notifications").json()["total"] == 1:
                return
            sleep(.1)
        self.fail("The background job did not create the scheduled reminder.")
