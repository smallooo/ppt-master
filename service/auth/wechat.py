"""WeChat code2session adapter + dev fallback.

When ``WECHAT_APPID`` is empty or starts with "replace_with_", we treat the
incoming ``code`` as the openid directly. This keeps local development
unblocked without needing real WeChat credentials.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from service.config import ServiceSettings


WECHAT_CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"


@dataclass(frozen=True)
class WeChatSession:
    openid: str
    unionid: str | None
    session_key: str | None


class WeChatAuthError(Exception):
    """Raised when code2session fails."""


def _is_dev_mode(settings: ServiceSettings) -> bool:
    appid = (settings.wechat_appid or "").strip()
    return not appid or appid.startswith("replace_with_")


def code2session(settings: ServiceSettings, code: str) -> WeChatSession:
    if not code:
        raise WeChatAuthError("Missing wechat code")

    if _is_dev_mode(settings):
        # Dev fallback: treat the code as a stable openid for local testing.
        return WeChatSession(
            openid=f"dev_{code}",
            unionid=None,
            session_key=None,
        )

    params = {
        "appid": settings.wechat_appid,
        "secret": settings.wechat_appsecret,
        "js_code": code,
        "grant_type": "authorization_code",
    }
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(WECHAT_CODE2SESSION_URL, params=params)
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPError as exc:
        raise WeChatAuthError(f"WeChat HTTP error: {exc}") from exc

    if "errcode" in payload and payload["errcode"]:
        raise WeChatAuthError(
            f"WeChat error {payload.get('errcode')}: {payload.get('errmsg')}"
        )
    openid = payload.get("openid")
    if not openid:
        raise WeChatAuthError("WeChat response missing openid")
    return WeChatSession(
        openid=openid,
        unionid=payload.get("unionid"),
        session_key=payload.get("session_key"),
    )
