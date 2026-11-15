from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.commitment_graph import extract, grounded_deadline
from app.commitment_model import LABELS, classifier_inputs, configured_model
from app.main import create_app


class CommitmentWorkflowTest(TestCase):
    def test_invalid_extraction_keeps_later_candidates_eligible(self):
        candidate = {
            "speaker": "Priya",
            "context": "",
            "quote": "I'll send the updated",
            "next_context": "Priya: checklist by Friday.",
        }
        with patch("app.commitment_graph.httpx.Client") as llm:
            post = llm.return_value.__enter__.return_value.post
            post.return_value.json.side_effect = [
                {"message": {"content": '{"invented_field":"invalid"}'}},
                {"message": {"content": '{"task":"Send the updated checklist","deadline":"Friday"}'}},
            ]
            result = extract({"candidates": [candidate, candidate]})

        self.assertTrue(result["extraction_unavailable"])
        first, second = result["candidates"]
        self.assertEqual(first, candidate)
        self.assertEqual(second["details"]["deadline"], "Friday")
        self.assertEqual(second["quote"], candidate["quote"])
        prompt = post.call_args.kwargs["json"]["messages"][-1]["content"]
        self.assertIn(candidate["next_context"], prompt)

    def test_first_person_owner_comes_from_transcript_speaker(self):
        quote = {"context": "", "quote": "I can call John today."}
        with patch("app.commitment_graph.httpx.Client") as llm:
            llm.return_value.__enter__.return_value.post.return_value.json.return_value = {
                "message": {"content": '{"owner":"John","task":"Call John","recipient":"John","deadline":"tomorrow"}'},
            }
            result = extract({"candidates": [{**quote, "speaker": "Priya"}, {**quote, "speaker": None}]})

        self.assertFalse(result["extraction_unavailable"])
        known, unknown = (item["details"] for item in result["candidates"])
        self.assertEqual(known, {"owner": "Priya", "task": "Call John", "recipient": "John", "deadline": "today"})
        self.assertEqual(unknown, {**known, "owner": None})

    def test_deadline_rejects_non_dates_and_ambiguous_fallbacks(self):
        self.assertIsNone(grounded_deadline("ongoing support", "We'll give them ongoing support.", "", ""))
        self.assertIsNone(grounded_deadline(None, "I'll send one by Friday and the other by Monday.", "", ""))
        self.assertIsNone(grounded_deadline(None, "I'll send it if finance approves today.", "", ""))
        self.assertEqual(grounded_deadline(None, "I'll send the report by Friday.", "", ""), "Friday")

    def test_extraction_prefers_timing_stated_in_its_own_commitment(self):
        self.assertEqual(grounded_deadline(
            "tomorrow afternoon", "I'll ship the first build this afternoon. It is still being styled.",
            "", "We'll review it tomorrow afternoon.",
        ), "this afternoon")
        self.assertEqual(grounded_deadline(
            "Friday at 5 pm", "I'll send the report by Friday at 5 pm.", "", "",
        ), "Friday at 5 pm")
        self.assertEqual(grounded_deadline(
            "Friday", "I'll send the report before Friday.", "", "",
        ), "before Friday")

    def test_regular_startup_uses_workspace_model_if_primary_export_is_missing(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "candidate"
            workspace.mkdir()
            (workspace / "config.json").write_text("{}", encoding="utf-8")
            with patch("app.commitment_model.DEFAULT_MODEL", Path(tmp) / "missing"), \
                 patch("app.commitment_model.WORKSPACE_MODEL", workspace), patch.dict("os.environ", {"PROMISE_KEEPER_CLASSIFIER": ""}):
                self.assertEqual(configured_model(), workspace)

    def test_recovers_unflagged_commitment_with_transcript_evidence(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.db"
            scores = dict.fromkeys(LABELS, 0.0)
            scores["non_commitment"] = 1.0
            with TestClient(create_app(path)) as client:
                user = client.post("/api/users", json={"name": "Priya", "email": "priya@example.com"}).json()
                meeting = client.post("/api/meetings", json={
                    "title": "Planning", "date": "2026-11-15", "owner_id": user["id"],
                }).json()
                base = f"/api/meetings/{meeting['id']}"
                quote = "I'll send the checklist by Friday."
                client.put(base + "/transcript", json={"text": "Priya: " + quote}).raise_for_status()
                url = base + "/commitments/manual"
                with patch("app.commitment_graph.predict", return_value=[scores]):
                    response = client.post(url)
                response.raise_for_status()
                review = response.json()
                self.assertEqual(review["status"], "review")
                self.assertEqual(review["candidates"], [])
                self.assertEqual(review["unflagged"][0]["quote"], quote)
            with TestClient(create_app(path)) as client:
                decision = {"revision": review["revision"], "approved": {"999": {"task": "Unrelated"}}}
                self.assertEqual(client.put(url, json=decision).status_code, 422)
                decision["approved"] = {"0": {"task": "Send checklist", "owner": "Priya"}}
                confirmed = client.put(url, json=decision)
                confirmed.raise_for_status()
                item = confirmed.json()["confirmed"][0]
                self.assertEqual((item["quote"], item["speaker"], item["label"]), (quote, "Priya", "manual"))

    def test_manual_review_recovers_a_failed_classifier_without_auto_saving(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.db"
            with TestClient(create_app(path)) as client:
                user = client.post("/api/users", json={"name": "Priya", "email": "priya@example.com"}).json()
                meeting = client.post("/api/meetings", json={
                    "title": "Planning", "date": "2026-11-15", "owner_id": user["id"],
                }).json()
                base = f"/api/meetings/{meeting['id']}"
                quote = "I'll send the checklist by Friday."
                client.put(base + "/transcript", json={"text": "Priya: " + quote}).raise_for_status()
                url = base + "/commitments/manual"
                with patch("app.commitment_graph.predict", side_effect=OSError("model unavailable")) as predict:
                    self.assertEqual(client.post(url).status_code, 503)
                    self.assertEqual(client.get(url).json()["status"], "processing")
                    response = client.post(url, params={"manual_review": True})
                    predict.assert_called_once()
                response.raise_for_status()
                review = response.json()
                self.assertTrue(review["manual_review"])
                self.assertEqual(review["status"], "review")
                self.assertEqual(review["candidates"], [])
                self.assertEqual(review["confirmed"], [])
                self.assertEqual(review["unflagged"][0]["quote"], quote)

            with TestClient(create_app(path)) as client:
                self.assertEqual(client.get(url).json(), review)
                decision = {"revision": review["revision"], "approved": {"0": {"task": "Send checklist"}}}
                result = client.put(url, json=decision).raise_for_status().json()
                self.assertEqual(result["status"], "complete")
                self.assertEqual(result["confirmed"][0]["quote"], quote)
                self.assertEqual(client.post(url, params={"manual_review": True}).json(), result)

    def test_adjacent_turns_are_optional_model_inputs(self):
        items = [{"context": "Priya: Who can send it?", "quote": "I can.", "next_context": "Priya: Thanks."}]
        self.assertEqual(classifier_inputs(items), (["Priya: Who can send it?"], ["I can."]))
        self.assertEqual(classifier_inputs(items, adjacent_context=True), (
            ["Previous: Priya: Who can send it?\nFollowing: Priya: Thanks."], ["I can."],
        ))

    def test_review_resumes_after_restart(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.db"
            scores = dict.fromkeys(LABELS, 0.01)
            scores["implicit_commitment"] = 0.95
            with TestClient(create_app(path)) as client:
                user = client.post("/api/users", json={"name": "Priya", "email": "priya@example.com"}).json()
                meeting = client.post("/api/meetings", json={
                    "title": "Planning", "date": "2026-11-15", "owner_id": user["id"],
                }).json()
                url = f"/api/meetings/{meeting['id']}"
                client.put(url + "/transcript", json={"text": "Priya: I'll send the checklist by Friday."})
                url += "/commitments/manual"
                self.assertIsNone(client.get(url).json())
                with patch("app.commitment_graph.predict", return_value=[scores]) as predict, patch("app.commitment_graph.httpx.Client") as llm:
                    llm.return_value.__enter__.return_value.post.return_value.json.return_value = {
                        "message": {"content": '{"task":"Send the checklist","deadline":"Friday"}'},
                    }
                    response = client.post(url)
                    self.assertEqual(predict.call_args.args[0][0]["next_context"], "")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["status"], "review")
            with TestClient(create_app(path)) as client:
                saved = client.get(url).json()
                decision = {"revision": saved["revision"], "approved": {"0": {"task": "Send checklist", "owner": "Priya"}}}
                response = client.put(url, json=decision)
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                self.assertEqual(result["status"], "complete")
                self.assertEqual(result["confirmed"][0]["quote"], "I'll send the checklist by Friday.")
                self.assertEqual(result["confirmed"][0]["details"]["owner"], "Priya")
                self.assertEqual(client.post(url).json(), result)
