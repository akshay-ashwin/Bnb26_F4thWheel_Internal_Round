from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.schemas.base import ApiRequest, ApiResponse


class ClaimIn(ApiRequest):
    admission_token: str = Field(min_length=1, max_length=4096)


class ClaimOut(ApiResponse):
    allocation_id: UUID
    seat_no: int
    confirmed_at: datetime
