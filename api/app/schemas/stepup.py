from typing import Literal

from pydantic import Field

from app.schemas.base import ApiRequest, ApiResponse


class StepUpIn(ApiRequest):
    otp: str = Field(min_length=1, max_length=16)


class StepUpOut(ApiResponse):
    status: Literal["OFFERED"]
