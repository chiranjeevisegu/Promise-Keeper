from datetime import date
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Email = Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class UserInput(BaseModel):
    name: Name
    email: Email


class UserRead(UserInput):
    model_config = ConfigDict(from_attributes=True)
    id: int


class MeetingInput(BaseModel):
    title: Title
    date: date
    owner_id: int = Field(gt=0)


class MeetingRead(MeetingInput):
    model_config = ConfigDict(from_attributes=True)
    id: int
    has_transcript: bool


class TranscriptInput(BaseModel):
    text: str = Field(max_length=200_000)


class TranscriptRead(TranscriptInput):
    model_config = ConfigDict(from_attributes=True)
    updated_at: str | None = None
