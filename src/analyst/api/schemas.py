"""Request/response models for the HTTP API."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from analyst.config import get_settings


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[\w .-]+$")
    kind: str = Field(examples=["postgres", "duckdb"])
    credentials: dict[str, Any] = Field(
        examples=[{"dsn": "postgresql://analyst_ro:password@host:5432/db"}],
        description="Connector-specific. Stored encrypted; never returned by the API.",
    )
    description: str = Field(default="", max_length=500)


class ThreadCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class RunRequest(BaseModel):
    message: str = Field(min_length=1, max_length=get_settings().max_question_chars)
    source_ids: list[str] = Field(default_factory=list, description="Restrict to these sources; empty = all.")


class Decision(BaseModel):
    action: Literal["approve", "reject", "edit"]
    sql: str | None = Field(default=None, max_length=20_000, description="Edited SQL when action=edit.")
    comment: str | None = Field(default=None, max_length=1000)


class ResumeRequest(BaseModel):
    decision: Decision
