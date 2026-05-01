"""Auth router — WeChat code2session login."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from service.auth.dependencies import get_current_token, get_current_user
from service.auth.sessions import TokenClaims, issue_token
from service.auth.users import (
    UserRecord,
    record_session,
    revoke_session,
    upsert_user,
)
from service.auth.wechat import WeChatAuthError, code2session
from service.config import ServiceSettings, get_settings
from service.schemas.auth import (
    AuthUserSummary,
    LogoutResponse,
    WechatLoginRequest,
    WechatLoginResponse,
)
from service.schemas.common import ResponseEnvelope


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post(
    "/wechat/login",
    response_model=ResponseEnvelope[WechatLoginResponse],
)
def wechat_login(
    request: WechatLoginRequest,
    settings: ServiceSettings = Depends(get_settings),
) -> ResponseEnvelope[WechatLoginResponse]:
    try:
        session = code2session(settings, request.code)
    except WeChatAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    user = upsert_user(
        openid=session.openid,
        unionid=session.unionid,
        nickname=request.nickname,
        avatar_url=request.avatar_url,
    )
    issued = issue_token(settings, user.id)
    record_session(user_id=user.id, token_id=issued.token_id, expires_at=issued.expires_at)

    return ResponseEnvelope(
        data=WechatLoginResponse(
            token=issued.token,
            expires_at=issued.expires_at,
            user=AuthUserSummary(
                user_id=user.id,
                openid=user.openid,
                nickname=user.nickname,
                avatar_url=user.avatar_url,
            ),
        )
    )


@router.post("/logout", response_model=ResponseEnvelope[LogoutResponse])
def logout(claims: TokenClaims = Depends(get_current_token)) -> ResponseEnvelope[LogoutResponse]:
    revoked = revoke_session(claims.token_id)
    return ResponseEnvelope(data=LogoutResponse(revoked=revoked))


me_router = APIRouter(prefix="/api/v1/mini", tags=["mini-user"])


@me_router.get("/me", response_model=ResponseEnvelope[AuthUserSummary])
def get_me(user: UserRecord = Depends(get_current_user)) -> ResponseEnvelope[AuthUserSummary]:
    return ResponseEnvelope(
        data=AuthUserSummary(
            user_id=user.id,
            openid=user.openid,
            nickname=user.nickname,
            avatar_url=user.avatar_url,
        )
    )
