from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import func, select, text

from .insights import analyze_history, current_risks, dependency_edges, graph_for, latest_evidence
from .ledger import normalized, promise_view
from .memory import Evidence
from .memory_routes import select_passages
from .models import EvidenceReview, Meeting, Promise, PromiseEvent, User
from .routes import Database, require_record

router = APIRouter(prefix="/api")
Owner = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]


class BriefingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner: Owner | None = None


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewer_id: int = Field(gt=0)
    event_id: int = Field(gt=0)
    revision: int = Field(ge=0)
    verdict: Literal["supports", "insufficient", "contradicts"]
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)] | None = None


def records_for(db, owner):
    query = select(Promise, Meeting).join(Meeting).order_by(Meeting.date.desc(), Promise.id.desc())
    if owner:
        query = query.where(Promise.owner_key == normalized(owner))
    return db.execute(query).all()


@router.get("/insights")
def insights(db: Database, owner: Annotated[Owner | None, Query()] = None,
             days: Annotated[int, Query(ge=7, le=365)] = 90):
    today = date.today()
    records = records_for(db, owner)
    start = today - timedelta(days=days - 1)
    history = analyze_history(db, [(promise, meeting) for promise, meeting in records if start <= meeting.date <= today], today)
    owners = db.execute(select(Promise.owner_key, func.min(Promise.owner)).where(Promise.owner_key != "")
                        .group_by(Promise.owner_key).order_by(Promise.owner_key))
    risks = [item for item in current_risks(db, records, today) if item["level"] != "closed"]
    links = {node for edge in dependency_edges(db) for node in edge}
    graph_records = sorted(records, key=lambda row: row[0].id not in links)[:100]
    graph_options = [{"id": promise.id, "task": promise.task, "meeting_title": meeting.title}
                     for promise, meeting in graph_records]
    return {
        **history, "risks": risks[:30], "risk_total": len(risks), "graph_options": graph_options,
        "owners": [name for _, name in owners], "from": start, "through": today,
    }


@router.get("/insights/dependencies/{promise_id}")
def dependencies(promise_id: int, db: Database):
    require_record(db, Promise, promise_id, "Promise not found.")
    return graph_for(db, promise_id, date.today())


@router.post("/insights/briefing")
def daily_briefing(body: BriefingRequest, db: Database):
    today = date.today()
    open_items = [item for item in current_risks(db, records_for(db, body.owner), today) if item["level"] != "closed"]
    counts = {
        "open": len(open_items), "high_risk": sum(item["level"] == "high" for item in open_items),
        "overdue": sum(item["status"] == "overdue" for item in open_items),
        "due_today": sum(item["due_date"] == today for item in open_items),
        "undated": sum(item["due_date"] is None for item in open_items),
    }
    candidates = [item for item in open_items if item["level"] in {"high", "watch", "insufficient"}][:12]
    db.rollback()
    mode, selected = "rules", candidates[:5]
    if candidates:
        passages = [
            Evidence(
                id=str(item["id"]), meeting_id=item["meeting_id"], meeting_title=item["meeting_title"],
                meeting_date=item["meeting_date"], source="ledger", line=0,
                quote=f"{item['task']}. {item['status']}. " + " ".join(item["reasons"]),
                speaker=item["owner"], start=None,
            ) for item in candidates
        ]
        try:
            picks = select_passages(
                "Which commitments need attention today? Prioritize overdue work and blocked work near its due date.",
                passages,
            )
            by_id = {str(item["id"]): item for item in candidates}
            allowed = {passage.id: passage.quote[:950] for passage in passages}
            ids = dict.fromkeys(pick.source_id for pick in picks if pick.source_id in allowed
                                and pick.quote.strip() and pick.quote in allowed[pick.source_id])
            if ids:
                # Keep every highest-priority item ahead of lower-priority suggestions.
                urgent = [str(item["id"]) for item in candidates if item["level"] == "high"]
                ordered = dict.fromkeys([*urgent, *ids, *(str(item["id"]) for item in candidates)])
                mode, selected = "ai", [by_id[key] for key in list(ordered)[:5]]
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            pass
    return {"date": today, "counts": counts, "items": selected, "mode": mode}


def evidence_event(db, promise):
    if promise.status != "completed":
        return None
    events = list(db.scalars(select(PromiseEvent).where(
        PromiseEvent.promise_id == promise.id,
    ).order_by(PromiseEvent.id)))
    return latest_evidence(events)


def evidence_view(db, promise):
    event = evidence_event(db, promise)
    reviews = list(db.scalars(select(EvidenceReview).where(
        EvidenceReview.event_id == event.id,
    ).order_by(EvidenceReview.id.desc()))) if event else []
    return {
        "promise": promise_view(promise, db.get(Meeting, promise.meeting_id)),
        "evidence": {"id": event.id, "submitted_by": event.actor_name, "submitted_at": event.recorded_at,
                     "note": event.evidence, "url": event.evidence_url} if event else None,
        "reviews": [{"id": review.id, "reviewer": review.reviewer_name, "verdict": review.verdict,
                     "note": review.note, "reviewed_at": review.reviewed_at} for review in reviews],
    }


@router.get("/promises/{promise_id}/evidence-review")
def get_review(promise_id: int, db: Database):
    return evidence_view(db, require_record(db, Promise, promise_id, "Promise not found."))


@router.post("/promises/{promise_id}/evidence-review")
def review_evidence(promise_id: int, body: ReviewRequest, db: Database):
    db.execute(text("BEGIN IMMEDIATE"))
    promise = require_record(db, Promise, promise_id, "Promise not found.")
    reviewer = require_record(db, User, body.reviewer_id, "Profile not found.")
    event = evidence_event(db, promise)
    if not event or event.id != body.event_id:
        raise HTTPException(409, "The current completion evidence has changed. Reload it before reviewing.")
    latest = db.scalar(select(EvidenceReview).where(EvidenceReview.event_id == event.id)
                       .order_by(EvidenceReview.id.desc()).limit(1))
    if body.revision != (latest.id if latest else 0):
        raise HTTPException(409, "Another evidence review was saved. Reload it before reviewing.")
    db.add(EvidenceReview(
        event_id=event.id, reviewer_id=reviewer.id, reviewer_name=reviewer.name, verdict=body.verdict,
        note=body.note, reviewed_at=datetime.now(timezone.utc).isoformat(),
    ))
    db.commit()
    return evidence_view(db, promise)
