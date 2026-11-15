from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .commitment_storage import delete_checkpoints, meeting_lock
from .database import session_for
from .models import AudioTranscript, Meeting, MemoryVector, Transcript, User
from .schemas import MeetingInput, MeetingRead, TranscriptInput, TranscriptRead, UserInput, UserRead

router = APIRouter(prefix="/api")


def get_session(request: Request):
    yield from session_for(request)


Database = Annotated[Session, Depends(get_session)]


def require_record(db, model, record_id, message):
    record = db.get(model, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=message)
    return record


def persist_user(db, user):
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="A profile already uses that email.") from error
    db.refresh(user)
    return user


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/users", response_model=list[UserRead])
def list_users(db: Database):
    return db.scalars(select(User).order_by(User.name, User.id)).all()


@router.post("/users", response_model=UserRead, status_code=201)
def create_user(body: UserInput, db: Database):
    user = User(**body.model_dump())
    db.add(user)
    return persist_user(db, user)


@router.put("/users/{user_id}", response_model=UserRead)
def update_user(user_id: int, body: UserInput, db: Database):
    user = require_record(db, User, user_id, "Profile not found.")
    user.name = body.name
    user.email = body.email
    return persist_user(db, user)


def meeting_read(meeting, has_transcript):
    return MeetingRead(
        id=meeting.id, title=meeting.title, date=meeting.date,
        owner_id=meeting.owner_id, has_transcript=has_transcript,
    )


@router.get("/meetings", response_model=list[MeetingRead])
def list_meetings(db: Database):
    has_transcript = select(Transcript.meeting_id).where(Transcript.meeting_id == Meeting.id).exists()
    has_transcript = has_transcript | select(AudioTranscript.meeting_id).where(AudioTranscript.meeting_id == Meeting.id).exists()
    rows = db.execute(select(Meeting, has_transcript).order_by(Meeting.date.desc(), Meeting.id.desc()))
    return [meeting_read(meeting, saved) for meeting, saved in rows]


@router.get("/meetings/{meeting_id}", response_model=MeetingRead)
def get_meeting(meeting_id: int, db: Database):
    meeting = require_record(db, Meeting, meeting_id, "Meeting not found.")
    return meeting_read(meeting, db.get(Transcript, meeting_id) is not None or db.get(AudioTranscript, meeting_id) is not None)


@router.post("/meetings", response_model=MeetingRead, status_code=201)
def create_meeting(body: MeetingInput, db: Database):
    require_record(db, User, body.owner_id, "Profile not found.")
    meeting = Meeting(**body.model_dump())
    db.add(meeting)
    db.commit()
    return meeting_read(meeting, False)


@router.put("/meetings/{meeting_id}", response_model=MeetingRead)
def update_meeting(meeting_id: int, body: MeetingInput, db: Database):
    meeting = require_record(db, Meeting, meeting_id, "Meeting not found.")
    require_record(db, User, body.owner_id, "Profile not found.")
    for field, value in body.model_dump().items():
        setattr(meeting, field, value)
    db.commit()
    return meeting_read(meeting, db.get(Transcript, meeting_id) is not None or db.get(AudioTranscript, meeting_id) is not None)


@router.delete("/meetings/{meeting_id}", status_code=204)
def delete_meeting(meeting_id: int, db: Database):
    with meeting_lock(db, meeting_id):
        db.delete(require_record(db, Meeting, meeting_id, "Meeting not found."))
        delete_checkpoints(db, meeting_id, "manual", "audio")
        db.commit()
    return Response(status_code=204)


@router.get("/meetings/{meeting_id}/transcript", response_model=TranscriptRead)
def get_transcript(meeting_id: int, db: Database):
    require_record(db, Meeting, meeting_id, "Meeting not found.")
    return db.get(Transcript, meeting_id) or TranscriptRead(text="")


@router.put("/meetings/{meeting_id}/transcript", response_model=TranscriptRead)
def save_transcript(meeting_id: int, body: TranscriptInput, db: Database):
    with meeting_lock(db, meeting_id):
        require_record(db, Meeting, meeting_id, "Meeting not found.")
        values = {"text": body.text, "updated_at": datetime.now(timezone.utc).isoformat()}
        statement = insert(Transcript).values(meeting_id=meeting_id, **values)
        try:
            db.execute(statement.on_conflict_do_update(index_elements=[Transcript.meeting_id], set_=values))
            db.execute(delete(MemoryVector).where(MemoryVector.meeting_id == meeting_id))
            db.commit()
        except IntegrityError as error:
            db.rollback()
            raise HTTPException(status_code=404, detail="Meeting not found.") from error
    return TranscriptRead(**values)
