from datetime import date, datetime, timezone

from sqlalchemy import ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(254), unique=True)


class WorkspaceSession(Base):
    __tablename__ = "workspace_sessions"

    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[int] = mapped_column(index=True)


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    date: Mapped[date]
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)


class Transcript(Base):
    __tablename__ = "transcripts"

    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(default=lambda: datetime.now(timezone.utc).isoformat())


class AudioTranscript(Base):
    __tablename__ = "audio_transcripts"

    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[str]
    segments: Mapped[list[dict]] = mapped_column(JSON)
    names: Mapped[dict[str, str]] = mapped_column(JSON)


class LedgerReview(Base):
    __tablename__ = "ledger_reviews"

    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), primary_key=True)
    source: Mapped[str] = mapped_column(primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(primary_key=True)


class Promise(Base):
    __tablename__ = "promises"
    __table_args__ = (UniqueConstraint("meeting_id", "source", "evidence_key"), {"sqlite_autoincrement": True})

    id: Mapped[int] = mapped_column(primary_key=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    source: Mapped[str]
    evidence_key: Mapped[str]
    line: Mapped[int]
    quote: Mapped[str] = mapped_column(Text)
    speaker: Mapped[str | None]
    start: Mapped[float | None]
    kind: Mapped[str]
    owner: Mapped[str | None]
    owner_key: Mapped[str] = mapped_column(index=True)
    task: Mapped[str] = mapped_column(Text)
    task_key: Mapped[str]
    recipient: Mapped[str | None]
    deadline: Mapped[str | None]
    status: Mapped[str] = mapped_column(default="pending", index=True)
    confirmed_at: Mapped[str]
    due_date: Mapped[date | None]
    lifecycle_version: Mapped[int] = mapped_column(default=0, server_default="0")


class PromiseDependency(Base):
    __tablename__ = "promise_dependencies"

    promise_id: Mapped[int] = mapped_column(ForeignKey("promises.id", ondelete="CASCADE"), primary_key=True)
    prerequisite_id: Mapped[int] = mapped_column(ForeignKey("promises.id", ondelete="CASCADE"), primary_key=True, index=True)


class PromiseEvent(Base):
    __tablename__ = "promise_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    promise_id: Mapped[int] = mapped_column(ForeignKey("promises.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    actor_name: Mapped[str]
    recorded_at: Mapped[str]
    changes: Mapped[dict] = mapped_column(JSON)
    evidence: Mapped[str | None] = mapped_column(Text)
    evidence_url: Mapped[str | None]


class EvidenceReview(Base):
    __tablename__ = "evidence_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("promise_events.id", ondelete="CASCADE"), index=True)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reviewer_name: Mapped[str]
    verdict: Mapped[str]
    note: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[str]

class NotificationPreferences(Base):
    __tablename__ = "notification_preferences"

    profile_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool]
    upcoming: Mapped[bool]
    overdue: Mapped[bool]
    daily_summary: Mapped[bool]
    lead_days: Mapped[int]
    summary_time: Mapped[str]
    timezone: Mapped[str]


class AppNotification(Base):
    __tablename__ = "app_notifications"
    __table_args__ = (UniqueConstraint("profile_id", "event_key"), {"sqlite_autoincrement": True})

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    promise_id: Mapped[int | None] = mapped_column(ForeignKey("promises.id", ondelete="CASCADE"), index=True)
    event_key: Mapped[str]
    kind: Mapped[str]
    title: Mapped[str]
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str]
    read_at: Mapped[str | None]


class MemoryVector(Base):
    __tablename__ = "memory_vectors"

    source_key: Mapped[str] = mapped_column(primary_key=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    fingerprint: Mapped[str]
    vector: Mapped[bytes]
