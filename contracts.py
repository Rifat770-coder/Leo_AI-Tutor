from typing import Literal

from pydantic import BaseModel, Field

Level = Literal["Beginner", "Intermediate", "Advanced"]
Language = Literal["English", "Bangla"]


class Preferences(BaseModel):
    level: Level = "Beginner"
    language: Language = "English"
    questions: int = Field(default=3, ge=2, le=10)


class SessionData(BaseModel):
    language: Language = "English"
    count: int = 3
    plan: dict | None = None
    lesson: str = ""
    quiz: dict | None = None
    quiz_id: str = ""
    draft: dict[int, str] = Field(default_factory=dict)
    conversation: list[tuple[str, str]] = Field(default_factory=list)
    rounds: int = 0
    guidance: dict | None = None
    error: str = ""
    failure_from: str = ""
    clarification: str = ""
    summary: str = ""


class RequestError(Exception):
    pass
