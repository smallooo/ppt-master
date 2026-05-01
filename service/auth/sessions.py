"""JWT session token issuance and verification."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt

from service.config import ServiceSettings


JWT_ALGORITHM = "HS256"


@dataclass(frozen=True)
class IssuedToken:
    token: str
    token_id: str
    expires_at: datetime


@dataclass(frozen=True)
class TokenClaims:
    user_id: str
    token_id: str
    expires_at: datetime


class TokenError(Exception):
    """Raised on invalid/expired tokens."""


def issue_token(settings: ServiceSettings, user_id: str) -> IssuedToken:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=settings.session_ttl_seconds)
    token_id = uuid4().hex
    payload = {
        "sub": user_id,
        "jti": token_id,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, settings.session_secret, algorithm=JWT_ALGORITHM)
    return IssuedToken(token=token, token_id=token_id, expires_at=expires_at)


def verify_token(settings: ServiceSettings, token: str) -> TokenClaims:
    try:
        payload = jwt.decode(
            token,
            settings.session_secret,
            algorithms=[JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError(f"Invalid token: {exc}") from exc

    user_id = payload.get("sub")
    token_id = payload.get("jti")
    exp = payload.get("exp")
    if not user_id or not token_id or not exp:
        raise TokenError("Token payload incomplete")
    return TokenClaims(
        user_id=user_id,
        token_id=token_id,
        expires_at=datetime.fromtimestamp(exp, tz=timezone.utc),
    )
