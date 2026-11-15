import re
from datetime import date, timedelta
from typing import Literal

from sqlalchemy import case

from .models import Promise

EditableStatus = Literal["pending", "in_progress", "unverified", "completed", "cancelled"]
Status = Literal["pending", "in_progress", "unverified", "completed", "overdue", "cancelled"]
ACTIVE = ("pending", "in_progress", "unverified")
WEEKDAYS = "monday tuesday wednesday thursday friday saturday sunday".split()
DATE_PHRASE = (
    r"\d{4}-\d{2}-\d{2}|today|tomorrow|(?:the )?end of (?:the )?day|"
    r"(?:this |next )?(?:" + "|".join(WEEKDAYS) + r")|in \d{1,3} days?"
)


def resolve_date(phrase, meeting_date):
    if not phrase:
        return None
    phrase = " ".join(phrase.lower().strip(" .!").split())
    phrase = re.sub(r"^(?:by|on) ", "", phrase)
    if not re.fullmatch(DATE_PHRASE, phrase):
        return None
    try:
        if phrase[0].isdigit():
            return date.fromisoformat(phrase)
        if phrase.startswith("in "):
            return meeting_date + timedelta(days=int(phrase.split()[1]))
        if phrase == "tomorrow":
            return meeting_date + timedelta(days=1)
        if phrase == "today" or "end of" in phrase:
            return meeting_date
        prefix, _, weekday = phrase.rpartition(" ")
        offset = WEEKDAYS.index(weekday) - meeting_date.weekday()
        if prefix == "next":
            offset += 7
        elif prefix == "this" and offset < 0:
            return None
        elif prefix != "this":
            offset %= 7
        return meeting_date + timedelta(days=offset)
    except (ValueError, OverflowError):
        return None


def lifecycle_status(promise, today=None):
    if promise.status in ACTIVE and promise.due_date and promise.due_date < (today or date.today()):
        return "overdue"
    return promise.status


def status_column(today=None):
    return case(
        (Promise.status.in_(ACTIVE) & (Promise.due_date < (today or date.today())), "overdue"),
        else_=Promise.status,
    )
