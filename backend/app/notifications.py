from collections import Counter, defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select, text
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from .ledger import normalized
from .models import AppNotification, NotificationPreferences, Promise, User
from .promise_dates import ACTIVE


def utc_now():
    return datetime.now(timezone.utc)


def schedule_notifications(db, *, now=None, profile_id=None):
    now = now or utc_now()
    # Serialize the settings and promise snapshot with lifecycle and preference writes.
    db.execute(text("BEGIN IMMEDIATE"))
    profiles = {profile.id: normalized(profile.name) for profile in db.scalars(select(User))}
    name_counts = Counter(profiles.values())
    query = select(NotificationPreferences).where(NotificationPreferences.enabled.is_(True))
    if profile_id is not None:
        query = query.where(NotificationPreferences.profile_id == profile_id)
    settings = [
        prefs for prefs in db.scalars(query)
        if profiles[prefs.profile_id] and name_counts[profiles[prefs.profile_id]] == 1
    ]
    if not settings:
        db.rollback()
        return 0

    promises = defaultdict(list)
    for promise in db.scalars(select(Promise).where(
        Promise.status.in_(ACTIVE), Promise.owner_key.in_({profiles[prefs.profile_id] for prefs in settings}),
    )):
        promises[promise.owner_key].append(promise)

    created = 0
    for prefs in settings:
        local = now.astimezone(ZoneInfo(prefs.timezone))
        records = promises[profiles[prefs.profile_id]]
        counts = Counter()
        for promise in records:
            if promise.due_date is None:
                counts["undated"] += 1
                continue
            days = (promise.due_date - local.date()).days
            if days < 0:
                bucket = "overdue"
            elif days == 0:
                bucket = "today"
            elif days <= prefs.lead_days:
                bucket = "upcoming"
            else:
                bucket = "later"
            counts[bucket] += 1
            if days < 0 and prefs.overdue:
                created += add_notification(db, prefs.profile_id, now, "overdue", "Commitment overdue",
                                            f"{promise.task} was due on {promise.due_date.isoformat()}.", promise)
            elif 0 <= days <= prefs.lead_days and prefs.upcoming:
                due = "today" if days == 0 else f"on {promise.due_date.isoformat()}"
                created += add_notification(db, prefs.profile_id, now, "upcoming", "Deadline reminder",
                                            f"{promise.task} is due {due}.", promise)
        if records and prefs.daily_summary and local.strftime("%H:%M") >= prefs.summary_time:
            summary = (
                f"{len(records)} open · {counts['overdue']} overdue · {counts['today']} due today · "
                f"{counts['upcoming']} due soon · {counts['later']} due later · {counts['undated']} without a date."
            )
            created += add_notification(db, prefs.profile_id, now, "summary", "Daily accountability summary",
                                        summary, summary_date=local.date())
    db.commit()
    return created


def add_notification(db, profile_id, now, kind, title, body, promise=None, summary_date=None):
    event_key = f"{kind}:{promise.id}:{promise.due_date}" if promise else f"{kind}:{summary_date}"
    statement = insert(AppNotification).values(
        profile_id=profile_id, promise_id=promise.id if promise else None, event_key=event_key,
        kind=kind, title=title, body=body, created_at=now.astimezone(timezone.utc).isoformat(),
    ).on_conflict_do_nothing(index_elements=["profile_id", "event_key"])
    return db.execute(statement).rowcount


def start_scheduler(engine, interval_seconds=60):
    def check():
        with Session(engine) as session:
            schedule_notifications(session)

    scheduler = BackgroundScheduler(timezone=timezone.utc)
    scheduler.add_job(check, "interval", seconds=interval_seconds, id="in-app-reminders",
                      next_run_time=utc_now(), coalesce=True, max_instances=1)
    scheduler.start()
    return scheduler


def follow_up(promise, today):
    opening = f"Hi {promise.owner}, " if promise.owner else "Hi, "
    timing = ""
    if promise.due_date:
        verb = "was" if promise.due_date < today else "is"
        timing = f" The recorded due date {verb} {promise.due_date.isoformat()}."
    return (
        f"{opening}checking in on this commitment: {promise.task.rstrip('.!?')}.{timing} "
        "Could you share your current status and flag any blockers or changes to the timeline?"
    )
