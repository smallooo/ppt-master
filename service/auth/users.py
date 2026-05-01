"""User repository — find or create users from WeChat openid."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from service.db import session_scope
from service.db import models as orm


@dataclass(frozen=True)
class UserRecord:
    id: str
    openid: str
    unionid: str | None
    nickname: str | None
    avatar_url: str | None


def upsert_user(
    *,
    openid: str,
    unionid: str | None = None,
    nickname: str | None = None,
    avatar_url: str | None = None,
) -> UserRecord:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        row = session.execute(
            select(orm.User).where(orm.User.openid == openid)
        ).scalar_one_or_none()
        if row is None:
            row = orm.User(
                openid=openid,
                unionid=unionid,
                nickname=nickname,
                avatar_url=avatar_url,
            )
            session.add(row)
            session.flush()
        else:
            row.unionid = unionid or row.unionid
            row.nickname = nickname or row.nickname
            row.avatar_url = avatar_url or row.avatar_url
            row.last_seen_at = now
        return UserRecord(
            id=row.id,
            openid=row.openid,
            unionid=row.unionid,
            nickname=row.nickname,
            avatar_url=row.avatar_url,
        )


def get_user(user_id: str) -> UserRecord | None:
    with session_scope() as session:
        row = session.get(orm.User, user_id)
        if row is None:
            return None
        return UserRecord(
            id=row.id,
            openid=row.openid,
            unionid=row.unionid,
            nickname=row.nickname,
            avatar_url=row.avatar_url,
        )


def record_session(*, user_id: str, token_id: str, expires_at: datetime) -> None:
    with session_scope() as session:
        session.add(
            orm.AuthSession(
                user_id=user_id,
                token_id=token_id,
                expires_at=expires_at,
            )
        )


def revoke_session(token_id: str) -> bool:
    """Mark the auth session row as revoked. Returns True if a row was updated."""
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        row = session.execute(
            select(orm.AuthSession).where(orm.AuthSession.token_id == token_id)
        ).scalar_one_or_none()
        if row is None:
            return False
        if row.revoked_at is None:
            row.revoked_at = now
        return True


def is_session_revoked(token_id: str) -> bool:
    with session_scope() as session:
        row = session.execute(
            select(orm.AuthSession).where(orm.AuthSession.token_id == token_id)
        ).scalar_one_or_none()
        return bool(row and row.revoked_at is not None)


def list_users(limit: int = 200) -> list[UserRecord]:
    with session_scope() as session:
        rows = (
            session.execute(
                select(orm.User).order_by(orm.User.created_at.desc()).limit(limit)
            )
            .scalars()
            .all()
        )
        return [
            UserRecord(
                id=r.id,
                openid=r.openid,
                unionid=r.unionid,
                nickname=r.nickname,
                avatar_url=r.avatar_url,
            )
            for r in rows
        ]
