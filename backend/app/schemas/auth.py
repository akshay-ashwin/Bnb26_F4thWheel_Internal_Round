from datetime import datetime

from pydantic import BaseModel


class OtpRequest(BaseModel):
    phone: str


class OtpRequestResponse(BaseModel):
    message: str
    expires_in_seconds: int
    demo_otp: str | None = None  # only populated when SIM_MODE=true


class OtpVerify(BaseModel):
    phone: str
    otp: str


class OtpVerifyResponse(BaseModel):
    session_token: str
    expires_at: datetime
    user_public_id: str
