"""Minimal OpenAI Chat Completions client (no SDK dependency).

Only the surface we actually need is exposed: a single ``chat`` call returning
the assistant message string. Errors raise :class:`OpenAIError`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import httpx

from service.config import ServiceSettings


class OpenAIError(Exception):
    """Raised when the OpenAI API call fails."""


@dataclass(frozen=True)
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


def chat(
    settings: ServiceSettings,
    *,
    model: str,
    messages: Iterable[ChatMessage],
    temperature: float = 0.4,
    response_format: dict | None = None,
    timeout: float = 120.0,
) -> str:
    if not settings.openai_api_key:
        raise OpenAIError("OPENAI_API_KEY is not configured")

    base = (settings.openai_base_url or "https://api.openai.com/v1").rstrip("/")
    url = f"{base}/chat/completions"
    payload: dict = {
        "model": model,
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        "temperature": temperature,
    }
    if response_format is not None:
        payload["response_format"] = response_format

    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        raise OpenAIError(
            f"OpenAI HTTP {exc.response.status_code}: {exc.response.text[:500]}"
        ) from exc
    except httpx.HTTPError as exc:
        raise OpenAIError(f"OpenAI request failed: {exc}") from exc

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise OpenAIError(f"Unexpected OpenAI response shape: {data}") from exc
