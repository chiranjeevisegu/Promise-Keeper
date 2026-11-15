from datetime import date
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert

from .models import AppNotification, NotificationPreferences, Promise, User
from .notifications import follow_up, schedule_notifications, utc_now
from .promise_dates import ACTIVE
from .routes import Database, require_record

router = APIRouter(prefix="/api")


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    upcoming: bool = True
    overdue: bool = True
    daily_summary: bool = True
    lead_days: int = Field(2, ge=0, le=30)
    summary_time: str = Field("09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    timezone: str = Field("UTC", min_length=1, max_length=80)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            return ZoneInfo(value).key
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("Enter an IANA timezone such as Asia/Kolkata.") from None


@router.get("/profiles/{profile_id}/notification-preferences")
def get_preferences(profile_id: int, db: Database):
    require_record(db, User, profile_id, "Profile not found.")
    stored = db.get(NotificationPreferences, profile_id)
    return {
        "settings": Preferences.model_validate(stored, from_attributes=True) if stored else Preferences(),
        "configured": stored is not None,
    }


@router.put("/profiles/{profile_id}/notification-preferences")
def save_preferences(profile_id: int, body: Preferences, db: Database):
    require_record(db, User, profile_id, "Profile not found.")
    settings = body.model_dump()
    statement = insert(NotificationPreferences).values(profile_id=profile_id, **settings)
    db.execute(statement.on_conflict_do_update(
        index_elements=["profile_id"], set_={key: getattr(statement.excluded, key) for key in settings},
    ))
    db.commit()
    return {"settings": body, "configured": True}


@router.get("/profiles/{profile_id}/notifications")
def inbox(profile_id: int, db: Database, offset: Annotated[int, Query(ge=0)] = 0):
    require_record(db, User, profile_id, "Profile not found.")
    scope = AppNotification.profile_id == profile_id
    total = db.scalar(select(func.count()).select_from(AppNotification).where(scope))
    unread = db.scalar(select(func.count()).select_from(AppNotification).where(scope, AppNotification.read_at.is_(None)))
    records = db.execute(select(AppNotification, Promise.status).outerjoin(Promise)
                         .where(scope).order_by(AppNotification.id.desc()).limit(30).offset(offset))
    return {
        "items": [{
            "id": record.id, "promise_id": record.promise_id, "kind": record.kind,
            "title": record.title, "body": record.body, "created_at": record.created_at,
            "read_at": record.read_at, "can_follow_up": status in ACTIVE,
        } for record, status in records],
        "total": total, "unread": unread,
    }


@router.post("/profiles/{profile_id}/notifications/check")
def check_now(profile_id: int, db: Database):
    require_record(db, User, profile_id, "Profile not found.")
    db.rollback()
    return {"created": schedule_notifications(db, profile_id=profile_id)}


@router.put("/profiles/{profile_id}/notifications/read")
def mark_read(profile_id: int, db: Database, notification_id: Annotated[int | None, Query(gt=0)] = None):
    require_record(db, User, profile_id, "Profile not found.")
    scope = AppNotification.profile_id == profile_id
    if notification_id is not None:
        scope = scope & (AppNotification.id == notification_id)
        if db.scalar(select(AppNotification.id).where(scope)) is None:
            raise HTTPException(404, "Notification not found.")
    db.execute(update(AppNotification).where(scope, AppNotification.read_at.is_(None))
               .values(read_at=utc_now().isoformat()))
    db.commit()
    return {"read": True}


@router.get("/promises/{promise_id}/follow-up")
def draft_follow_up(promise_id: int, db: Database):
    promise = require_record(db, Promise, promise_id, "Promise not found.")
    if promise.status not in ACTIVE:
        raise HTTPException(409, "Follow-up drafts are available for open commitments.")
    return {"draft": follow_up(promise, date.today())}
