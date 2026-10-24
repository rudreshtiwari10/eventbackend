from typing import Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    username: str


class GenerateRequest(BaseModel):
    count: int = Field(gt=0, le=5000)


class VolunteerCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=6, max_length=100)


class CodeRequest(BaseModel):
    code: str  # full UUID from a QR scan, or the 8-char short code typed by hand


class TicketOut(BaseModel):
    ticket_id: str
    short_code: str
    status: str
    used_at: Optional[str] = None
    scanned_by: Optional[str] = None
    cancelled_at: Optional[str] = None
    seq: int
