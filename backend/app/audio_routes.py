from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError

from . import audio
from .commitment_storage import delete_checkpoints, meeting_lock
from .models import AudioTranscript, LedgerReview, Meeting, MemoryVector, Promise
from .routes import Database, require_record

router = APIRouter(prefix="/api")
SpeakerName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
SegmentText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)]


class Segment(BaseModel):
    start: float
    end: float
    text: str
    speaker: str | None


class AudioRead(BaseModel):
    version: str
    segments: list[Segment]
    names: dict[str, str]


class AudioEdit(BaseModel):
    version: str
    texts: list[SegmentText] = Field(max_length=20_000)
    names: dict[str, SpeakerName] = Field(max_length=1000)
    speakers: list[SpeakerName | None] | None = Field(default=None, max_length=20_000)


def read(entry):
    return AudioRead(version=entry.version, segments=entry.segments, names=entry.names)


@router.get("/audio/capabilities")
def capabilities():
    return {"diarization": audio.diarization_available()}


@router.get("/meetings/{meeting_id}/audio-transcript", response_model=AudioRead | None)
def get_audio(meeting_id: int, db: Database):
    require_record(db, Meeting, meeting_id, "Meeting not found.")
    entry = db.get(AudioTranscript, meeting_id)
    return read(entry) if entry else None


@router.post("/meetings/{meeting_id}/audio-transcript", response_model=AudioRead, status_code=201)
def upload_audio(
    meeting_id: int, db: Database, file: Annotated[UploadFile, File()],
    diarize: Annotated[bool, Form()] = False,
    language: Annotated[Literal["auto", "en"], Form()] = "auto",
):
    require_record(db, Meeting, meeting_id, "Meeting not found.")
    if db.get(AudioTranscript, meeting_id):
        raise HTTPException(409, "Delete the saved audio transcript before transcribing another recording.")
    extension = Path(file.filename or "").suffix.lower()
    if extension not in audio.FORMATS:
        raise HTTPException(415, "Choose an MP3, WAV, M4A, or MP4 recording.")
    if not file.size or file.size > audio.MAX_BYTES:
        raise HTTPException(413, "Choose a non-empty recording up to 100 MB.")
    db.rollback()
    segments = audio.transcribe(file.file, extension, diarize, language=None if language == "auto" else language)
    entry = AudioTranscript(meeting_id=meeting_id, version=uuid4().hex, segments=segments, names={})
    db.add(entry)
    result = read(entry)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409, "The meeting or transcript changed. Reload the page and retry.") from error
    return result


@router.put("/meetings/{meeting_id}/audio-transcript", response_model=AudioRead)
def edit_audio(meeting_id: int, body: AudioEdit, db: Database):
    with meeting_lock(db, meeting_id):
        entry = require_record(db, AudioTranscript, meeting_id, "Audio transcript not found.")
        speakers = body.speakers if body.speakers is not None else [segment["speaker"] for segment in entry.segments]
        if len(body.texts) != len(entry.segments) or len(speakers) != len(entry.segments) or not body.names.keys() <= set(speakers):
            raise HTTPException(422, "Transcript segments or speaker IDs do not match.")
        segments = [dict(segment, text=text, speaker=speaker)
                    for segment, text, speaker in zip(entry.segments, body.texts, speakers, strict=True)]
        version = uuid4().hex
        result = db.execute(update(AudioTranscript).where(
            AudioTranscript.meeting_id == meeting_id, AudioTranscript.version == body.version,
        ).values(segments=segments, names=body.names, version=version))
        if not result.rowcount:
            raise HTTPException(409, "The audio transcript changed. Reload the page before saving.")
        db.execute(delete(MemoryVector).where(MemoryVector.meeting_id == meeting_id))
        db.commit()
    return AudioRead(version=version, segments=segments, names=body.names)


@router.delete("/meetings/{meeting_id}/audio-transcript", status_code=204)
def delete_audio(meeting_id: int, db: Database):
    with meeting_lock(db, meeting_id):
        db.delete(require_record(db, AudioTranscript, meeting_id, "Audio transcript not found."))
        delete_checkpoints(db, meeting_id, "audio")
        db.execute(delete(Promise).where(Promise.meeting_id == meeting_id, Promise.source == "audio"))
        db.execute(delete(LedgerReview).where(LedgerReview.meeting_id == meeting_id, LedgerReview.source == "audio"))
        db.execute(delete(MemoryVector).where(MemoryVector.meeting_id == meeting_id))
        db.commit()
