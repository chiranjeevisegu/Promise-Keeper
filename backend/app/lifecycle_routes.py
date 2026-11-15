import re
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints
from sqlalchemy import delete, select, text

from .ledger import normalized, promise_view
from .memory import evidence_documents
from .memory_routes import Scope
from .models import Meeting, Promise, PromiseDependency, PromiseEvent, User
from .promise_dates import DATE_PHRASE, EditableStatus, lifecycle_status, resolve_date
from .routes import Database, require_record

router = APIRouter(prefix="/api/promises")
RESCHEDULE = re.compile(
    r"\b(?:(?:move[ds]?|moving|push(?:ed|ing)?(?: back)?|postpon\w+|reschedul\w+|extend\w*|"
    r"shift\w*|chang\w*|switch\w*)\b[^.!?]{0,160}?\b(?:to|until)|"
    r"(?:new deadline|deadline)\b[^.!?]{0,120}?\b(?:is now|will be|is))\s+(" + DATE_PHRASE + r")\b",
    re.IGNORECASE,
)
TASK_STOP = frozenset("the a an for to of on by and is send deliver finish make prepare complete update please".split())
NEGATED = re.compile(r"\b(?:not|never|don['’]t|cannot|can['’]t)\b[^.!?]{0,20}$", re.IGNORECASE)
EvidenceNote = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
PromiseId = Annotated[int, Field(gt=0)]


class LifecycleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor_id: PromiseId
    version: int = Field(ge=0)
    status: EditableStatus
    due_date: date | None
    prerequisites: list[PromiseId] = Field(max_length=50)
    confirm_completion: bool = False
    evidence: EvidenceNote | None = None
    evidence_url: HttpUrl | None = None


def dependency_view(promise, meeting):
    return {"id": promise.id, "task": promise.task, "meeting_title": meeting.title,
            "owner": promise.owner, "status": lifecycle_status(promise)}


def deadline_suggestions(db, promise, meeting):
    terms = set(promise.task_key.split()) - TASK_STOP
    if not terms:
        return []
    suggestions = {}
    for doc in evidence_documents(db, Scope(date_from=meeting.date)):
        if (doc.meeting_id, doc.quote) == (meeting.id, promise.quote) or doc.promise_id == promise.id:
            continue
        if len(terms & set(normalized(doc.quote).split())) < min(2, len(terms)):
            continue
        if (match := RESCHEDULE.search(doc.quote)) and not NEGATED.search(doc.quote[:match.start()]):
            proposed = resolve_date(match[1], date.fromisoformat(doc.meeting_date))
            if proposed and proposed != promise.due_date:
                suggestions.setdefault(proposed, {
                    "date": proposed, "quote": doc.quote, "meeting_id": doc.meeting_id,
                    "meeting_title": doc.meeting_title, "start": doc.start,
                })
    return list(suggestions.values())[:6]


def lifecycle_view(db, promise):
    meeting = db.get(Meeting, promise.meeting_id)
    prerequisites = db.execute(
        select(Promise, Meeting).join(Meeting).join(
            PromiseDependency, PromiseDependency.prerequisite_id == Promise.id,
        ).where(PromiseDependency.promise_id == promise.id).order_by(Promise.id),
    )
    dependencies = [dependency_view(record, source) for record, source in prerequisites]
    history = db.scalars(select(PromiseEvent).where(PromiseEvent.promise_id == promise.id).order_by(PromiseEvent.id.desc()))
    return {
        "promise": promise_view(promise, meeting),
        "prerequisites": dependencies,
        "deadline_suggestions": deadline_suggestions(db, promise, meeting),
        "history": [{
            "id": event.id, "actor": event.actor_name, "recorded_at": event.recorded_at,
            "changes": event.changes, "evidence": event.evidence, "evidence_url": event.evidence_url,
        } for event in history],
    }


@router.get("/{promise_id}/lifecycle")
def get_lifecycle(promise_id: int, db: Database):
    return lifecycle_view(db, require_record(db, Promise, promise_id, "Promise not found."))


@router.get("/{promise_id}/dependency-options")
def dependency_options(promise_id: int, db: Database, q: Annotated[str, Query(max_length=150)] = ""):
    require_record(db, Promise, promise_id, "Promise not found.")
    query = select(Promise, Meeting).join(Meeting).where(Promise.id != promise_id)
    if q.strip():
        query = query.where(
            Promise.task.icontains(q.strip(), autoescape=True)
            | Promise.owner.icontains(q.strip(), autoescape=True)
            | Meeting.title.icontains(q.strip(), autoescape=True),
        )
    return [dependency_view(record, meeting) for record, meeting in db.execute(
        query.order_by(Promise.id.desc()).limit(30),
    )]


def check_cycle(db, promise_id, proposed):
    edges = defaultdict(list)
    for parent, child in db.execute(select(PromiseDependency.promise_id, PromiseDependency.prerequisite_id)):
        edges[parent].append(child)
    pending = list(proposed)
    visited = set()
    while pending:
        node = pending.pop()
        if node == promise_id:
            raise HTTPException(422, "That dependency would create a circular dependency.")
        if node not in visited:
            visited.add(node)
            pending.extend(edges[node])


@router.put("/{promise_id}/lifecycle")
def update_lifecycle(promise_id: int, body: LifecycleUpdate, db: Database):
    # Check the version and dependency graph under SQLite's write lock.
    db.execute(text("BEGIN IMMEDIATE"))
    promise = require_record(db, Promise, promise_id, "Promise not found.")
    if body.version != promise.lifecycle_version:
        raise HTTPException(409, "This promise changed in another view. Reload its lifecycle before saving.")
    actor = require_record(db, User, body.actor_id, "Profile not found.")
    proposed = set(body.prerequisites)
    existing = set(db.scalars(select(PromiseDependency.prerequisite_id).where(PromiseDependency.promise_id == promise_id)))
    records = list(db.scalars(select(Promise).where(Promise.id.in_(proposed)))) if proposed else []
    if len(records) != len(proposed) or len(proposed) != len(body.prerequisites):
        raise HTTPException(422, "Select distinct existing commitments as prerequisites.")
    if proposed != existing:
        check_cycle(db, promise_id, proposed)
    evidence_url = str(body.evidence_url) if body.evidence_url else None
    attaches_evidence = body.evidence is not None or evidence_url is not None
    if body.status == "completed":
        if any(record.status != "completed" for record in records):
            raise HTTPException(409, "Complete all prerequisites before closing this commitment.")
        if (promise.status != "completed" or attaches_evidence) and not body.confirm_completion:
            raise HTTPException(422, "Explicitly confirm completion before saving completion evidence.")
        if promise.status != "completed" and not attaches_evidence:
            raise HTTPException(422, "Add a note or link as completion evidence.")
    else:
        if attaches_evidence:
            raise HTTPException(422, "Completion evidence belongs to a completed commitment.")
        if promise.status == "completed" and db.scalar(select(Promise.id).join(
            PromiseDependency, PromiseDependency.promise_id == Promise.id,
        ).where(PromiseDependency.prerequisite_id == promise_id, Promise.status == "completed").limit(1)):
            raise HTTPException(409, "Reopen completed dependent commitments before reopening their prerequisite.")

    old_date = promise.due_date.isoformat() if promise.due_date else None
    new_date = body.due_date.isoformat() if body.due_date else None
    changes = {}
    for field, before, after in (
        ("status", promise.status, body.status), ("due_date", old_date, new_date),
        ("prerequisites", sorted(existing), sorted(proposed)),
    ):
        if before != after:
            changes[field] = {"from": before, "to": after}
    if changes or attaches_evidence:
        if existing - proposed:
            db.execute(delete(PromiseDependency).where(
                PromiseDependency.promise_id == promise_id, PromiseDependency.prerequisite_id.in_(existing - proposed),
            ))
        db.add_all(PromiseDependency(promise_id=promise_id, prerequisite_id=record_id) for record_id in proposed - existing)
        promise.status = body.status
        promise.due_date = body.due_date
        promise.lifecycle_version += 1
        db.add(PromiseEvent(
            promise_id=promise_id, actor_id=actor.id, actor_name=actor.name,
            recorded_at=datetime.now(timezone.utc).isoformat(), changes=changes,
            evidence=body.evidence, evidence_url=evidence_url,
        ))
        db.commit()
    else:
        db.rollback()
    return lifecycle_view(db, promise)
