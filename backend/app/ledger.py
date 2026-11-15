import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import inspect, select, text
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from .models import LedgerReview, Meeting, Promise
from .promise_dates import lifecycle_status, resolve_date


def normalized(value):
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", value or "").casefold()))


def save_review(db, meeting_id, source, snapshot):
    state = snapshot.values
    if snapshot.next or snapshot.interrupts or "confirmed" not in state:
        return
    checkpoint_id = snapshot.config["configurable"]["checkpoint_id"]
    decision = insert(LedgerReview).values(meeting_id=meeting_id, source=source, checkpoint_id=checkpoint_id)
    if not db.execute(decision.on_conflict_do_nothing()).rowcount:
        db.rollback()
        return

    occurrences = Counter()
    approved = {item["id"]: item for item in state["confirmed"]}
    confirmed_at = snapshot.created_at or datetime.now(timezone.utc).isoformat()
    for index, item in enumerate(state["items"]):
        evidence = (item["quote"], item["speaker"], item["start"])
        occurrences[evidence] += 1
        if index not in approved:
            continue
        evidence_key = hashlib.sha256(json.dumps((*evidence, occurrences[evidence])).encode()).hexdigest()
        item = approved[index]
        details = item["details"]
        record = insert(Promise).values(
            meeting_id=meeting_id, source=source, evidence_key=evidence_key, line=index,
            quote=item["quote"], speaker=item["speaker"], start=item["start"], kind=item["label"],
            owner=details["owner"], owner_key=normalized(details["owner"]), task=details["task"],
            task_key=normalized(details["task"]), recipient=details["recipient"], deadline=details["deadline"],
            confirmed_at=confirmed_at,
        )
        db.execute(record.on_conflict_do_update(
            index_elements=["meeting_id", "source", "evidence_key"],
            set_={key: getattr(record.excluded, key) for key in (
                "line", "kind", "owner", "owner_key", "task", "task_key", "recipient", "deadline",
            )},
        ))
    db.commit()


def backfill_ledger(engine):
    """Import completed Phase 3 checkpoints using the same idempotent write path."""
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langsmith import tracing_context

    from .commitment_graph import build_graph
    from .commitment_storage import meeting_lock

    with Session(engine) as db:
        if not inspect(engine).has_table("checkpoints"):
            return
        threads = db.scalars(text("SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE 'commitments:%'")).all()
        meeting_ids = set(db.scalars(select(Meeting.id)))
        db.rollback()
        with tracing_context(enabled=False), SqliteSaver.from_conn_string(engine.url.database) as saver:
            graph = build_graph(saver)
            for thread in threads:
                match = re.fullmatch(r"commitments:(\d+):(audio|manual)", thread)
                if match and int(match[1]) in meeting_ids:
                    meeting_id = int(match[1])
                    with meeting_lock(db, meeting_id):
                        snapshot = graph.get_state({"configurable": {"thread_id": thread}})
                        save_review(db, meeting_id, match[2], snapshot)


def promise_view(promise, meeting):
    return {
        "id": promise.id, "meeting_id": meeting.id, "meeting_title": meeting.title,
        "meeting_date": meeting.date, "source": promise.source, "line": promise.line,
        "quote": promise.quote, "speaker": promise.speaker, "start": promise.start, "kind": promise.kind,
        "owner": promise.owner, "task": promise.task, "recipient": promise.recipient,
        "deadline": promise.deadline, "due_date": promise.due_date,
        "suggested_due_date": resolve_date(promise.deadline, meeting.date) if not promise.due_date else None,
        "status": lifecycle_status(promise), "workflow_status": promise.status,
        "lifecycle_version": promise.lifecycle_version, "confirmed_at": promise.confirmed_at,
    }
