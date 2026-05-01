"""Auth-related schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class WechatLoginRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=512)
    nickname: str | None = Field(default=None, max_length=128)
    avatar_url: str | None = Field(default=None, max_length=512)


class AuthUserSummary(BaseModel):
    user_id: str
    openid: str
    nickname: str | None = None
    avatar_url: str | None = None


class WechatLoginResponse(BaseModel):
    token: str
    expires_at: datetime
    user: AuthUserSummary


class LogoutResponse(BaseModel):
    revoked: bool
