from typing import Literal
from uuid import UUID

from app.schemas.base import ApiRequest, ApiResponse


class EntryIn(ApiRequest):
    pass


class EntryOut(ApiResponse):
    entry_id: UUID
    status: Literal["REGISTERED"]
