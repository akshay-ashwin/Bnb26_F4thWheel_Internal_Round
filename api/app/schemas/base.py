"""Base models. Every response carries server_time; requests reject unknown fields."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.clock import server_time

Phase = Literal["SCHEDULED", "OPEN", "CLOSED", "DRAWN", "CLAIMING", "DONE"]
Mode = Literal["fair", "fifo"]
EntryStatus = Literal[
    "REGISTERED",
    "OFFERED",
    "STEP_UP_REQUIRED",
    "WAITLISTED",
    "NOT_SELECTED",
    "OFFER_EXPIRED",
    "ALLOCATED",
    "DISQUALIFIED",
]


class ApiRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ApiResponse(BaseModel):
    # Without this flag the default_factory would make server_time optional in the OpenAPI spec.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    server_time: str = Field(
        default_factory=server_time,
        description="ISO 8601 UTC with milliseconds, e.g. 2026-10-04T10:15:30.123Z",
    )


class ApiObject(BaseModel):
    """Nested response object (no server_time of its own)."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
