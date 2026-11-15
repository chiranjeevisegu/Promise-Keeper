import hashlib
import json
import re
from contextlib import contextmanager
from typing import Literal

from fastapi import APIRouter, HTTPException
from langsmith import tracing_context
from pydantic import BaseModel, Field

from .commitment_graph import Details, build_graph
from .commitment_storage import meeting_lock
from .ledger import save_review
from .models import AudioTranscript, Meeting, Transcript
from .routes import Database, require_record

router = APIRouter(prefix="/api")
Source = Literal["manual", "audio"]


class Decision(BaseModel):
    approved: dict[str, Details] = Field(max_length=2000)
    revision: str


def inputs(db, meeting_id, source):
    require_record(db, Meeting, meeting_id, "Meeting not found.")
    items = []
    if source == "audio":
        record = db.get(AudioTranscript, meeting_id)
        if record:
            items = [
                {"quote": row["text"], "speaker": record.names.get(row["speaker"], row["speaker"]), "start": row["start"]}
                for row in record.segments if row["text"].strip()
            ]
    else:
        record = db.get(Transcript, meeting_id)
        if record:
            for line in record.text.splitlines():
                speaker, sep, utterance = line.partition(":")
                speaker, text = (speaker.strip(), utterance) if sep and 0 < len(speaker.strip()) <= 40 else (None, line)
                for quote in re.split(r"(?<=[.!?])\s+", text.strip()):
                    if quote:
                        items.append({"quote": quote, "speaker": speaker, "start": None})
    previous = ""
    for item in items:
        item["context"] = previous
        previous = f"{item['speaker'] or 'Unknown speaker'}: {item['quote']}"
    return items


@contextmanager
def workflow(db, meeting_id, source):
    from langgraph.checkpoint.sqlite import SqliteSaver

    with meeting_lock(db, meeting_id):
        items = inputs(db, meeting_id, source)
        file = db.get_bind().url.database
        db.rollback()
        revision = hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()
        config = {"configurable": {"thread_id": f"commitments:{meeting_id}:{source}"}}
        with tracing_context(enabled=False), SqliteSaver.from_conn_string(file) as saver:
            yield build_graph(saver), saver, config, revision, items


def result(graph, config, revision, db, meeting_id, source):
    snapshot = graph.get_state(config)
    values = snapshot.values
    save_review(db, meeting_id, source, snapshot)
    if values.get("revision") != revision:
        return None
    reviewing = bool(snapshot.interrupts)
    candidates = values.get("candidates", [])
    detected = {candidate["id"] for candidate in candidates}
    return {
        "revision": revision,
        "status": "review" if reviewing else "processing" if snapshot.next else "complete",
        "candidates": candidates,
        "unflagged": [
            {"id": index, "quote": item["quote"], "speaker": item["speaker"], "start": item["start"]}
            for index, item in enumerate(values.get("items", [])) if index not in detected
        ] if reviewing else [],
        "confirmed": values.get("confirmed", []),
        "extraction_unavailable": values.get("extraction_unavailable", False),
        "manual_review": values.get("manual_review", False),
    }


@router.get("/meetings/{meeting_id}/commitments/{source}")
def get_analysis(meeting_id: int, source: Source, db: Database):
    with workflow(db, meeting_id, source) as (graph, _, config, revision, _):
        return result(graph, config, revision, db, meeting_id, source)


@router.post("/meetings/{meeting_id}/commitments/{source}")
def analyze(meeting_id: int, source: Source, db: Database, extract_details: bool = True, manual_review: bool = False):
    with workflow(db, meeting_id, source) as (graph, saver, config, revision, items):
        if not items:
            raise HTTPException(422, "Save a transcript before analyzing it.")
        existing = result(graph, config, revision, db, meeting_id, source)
        if existing and existing["status"] != "processing":
            return existing
        if not existing or manual_review:
            saver.delete_thread(config["configurable"]["thread_id"])
            existing = None
        try:
            graph.invoke(None if existing else {
                "items": items, "revision": revision, "extract_details": extract_details,
                "manual_review": manual_review,
            }, config)
        except (ImportError, OSError, RuntimeError) as error:
            raise HTTPException(503, "The local classifier could not run. You can review the transcript manually.") from error
        return result(graph, config, revision, db, meeting_id, source)


@router.put("/meetings/{meeting_id}/commitments/{source}")
def confirm(meeting_id: int, source: Source, body: Decision, db: Database):
    from langgraph.types import Command

    with workflow(db, meeting_id, source) as (graph, _, config, revision, _):
        current = result(graph, config, revision, db, meeting_id, source)
        if revision != body.revision or not current or current["status"] != "review":
            raise HTTPException(409, "This review is no longer current. Reload the page.")
        allowed = {str(item["id"]) for item in (*current["candidates"], *current["unflagged"])}
        if not body.approved.keys() <= allowed or any(not details.task or not details.task.strip() for details in body.approved.values()):
            raise HTTPException(422, "Choose saved transcript lines and enter a task for each approved item.")
        graph.invoke(Command(resume={key: details.model_dump() for key, details in body.approved.items()}), config)
        return result(graph, config, revision, db, meeting_id, source)
