from typing import Literal

from app.schemas.base import ApiResponse


class HealthOut(ApiResponse):
    status: Literal["ok"] = "ok"


class ReadyOut(ApiResponse):
    status: Literal["ready", "degraded"]
    postgres: Literal["up"]
    redis: Literal["up", "down"]
