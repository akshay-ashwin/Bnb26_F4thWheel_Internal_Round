from pydantic import Field

from app.schemas.base import ApiRequest, ApiResponse


class OtpRequestIn(ApiRequest):
    phone: str = Field(min_length=1, max_length=32)
    device_id: str = Field(min_length=1, max_length=128)


class OtpRequestOut(ApiResponse):
    request_id: str
    expires_in_s: int
    dev_otp: str | None = Field(default=None, description="SIM_MODE only; null otherwise.")


class OtpVerifyIn(ApiRequest):
    request_id: str = Field(min_length=1, max_length=64)
    otp: str = Field(min_length=1, max_length=16)
    device_id: str = Field(min_length=1, max_length=128)


class OtpVerifyOut(ApiResponse):
    session_token: str
    user_public_id: str
