#!/usr/bin/env python3
"""
Volcengine Seedream image generation backend.

Configuration keys:
    VOLCENGINE_API_KEY / ARK_API_KEY   (required)
    VOLCENGINE_BASE_URL                (optional)
    VOLCENGINE_MODEL                   (optional)

This backend targets the official Ark image API.
"""

import os
import time
import math

import requests

from image_backends.backend_common import (
    MAX_RETRIES,
    download_image,
    http_error,
    is_rate_limit_error,
    normalize_image_size,
    require_api_key,
    resolve_output_path,
    retry_delay,
)


DEFAULT_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/images/generations"
DEFAULT_MODEL = "doubao-seedream-4-5-251128"

SUPPORTED_SIZES = {"512px", "1K", "2K", "4K"}
SEEDREAM_45_MIN_PIXELS = 3686400
SEEDREAM_45_DIMENSION_STEP = 64


def _resolve_url(base_url: str) -> str:
    """Resolve the Volcengine generation endpoint."""
    base = base_url.rstrip("/")
    if base.endswith("/images/generations"):
        return base
    return base + "/images/generations"


def _resolve_size(aspect_ratio: str, image_size: str, model: str) -> str:
    """Resolve the Ark logical size preset for a ratio and image size."""
    if model.startswith("doubao-seedream-4-5"):
        return _resolve_seedream_45_size(aspect_ratio)

    normalized = normalize_image_size(image_size)
    if normalized not in SUPPORTED_SIZES:
        raise ValueError(
            f"Unsupported image size '{image_size}' for Volcengine backend. "
            f"Supported: {sorted(SUPPORTED_SIZES)}"
        )
    return normalized


def _resolve_seedream_45_size(aspect_ratio: str) -> str:
    """Seedream 4.5 requires explicit dimensions above a minimum pixel count."""
    try:
        width_ratio, height_ratio = aspect_ratio.split(":", 1)
        width_factor = int(width_ratio)
        height_factor = int(height_ratio)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"Unsupported aspect ratio '{aspect_ratio}' for Seedream 4.5."
        ) from exc

    width = math.sqrt(SEEDREAM_45_MIN_PIXELS * width_factor / height_factor)
    width_px = _round_up(width, SEEDREAM_45_DIMENSION_STEP)
    height_px = _round_up(width_px * height_factor / width_factor, SEEDREAM_45_DIMENSION_STEP)
    while width_px * height_px < SEEDREAM_45_MIN_PIXELS:
        height_px += SEEDREAM_45_DIMENSION_STEP

    return f"{width_px}x{height_px}"


def _round_up(value: float, step: int) -> int:
    return int(math.ceil(value / step) * step)


def _generate_image(api_key: str, prompt: str, negative_prompt: str = None,
                    aspect_ratio: str = "1:1", image_size: str = "1K",
                    output_dir: str = None, filename: str = None,
                    model: str = DEFAULT_MODEL, base_url: str = DEFAULT_ENDPOINT) -> str:
    """Generate one image with the Volcengine backend."""
    size = _resolve_size(aspect_ratio, image_size, model)
    url = _resolve_url(base_url)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "response_format": "url",
        "watermark": False,
    }
    if negative_prompt:
        payload["negative_prompt"] = negative_prompt

    print("[Volcengine Seedream]")
    print(f"  Model:        {model}")
    print(f"  Prompt:       {prompt[:120]}{'...' if len(prompt) > 120 else ''}")
    print(f"  Aspect Ratio: {aspect_ratio}")
    print(f"  Size:         {size}")
    print()
    print("  [..] Generating...", end="", flush=True)
    start = time.time()
    response = requests.post(url, headers=headers, json=payload, timeout=300)
    elapsed = time.time() - start
    print(f"\n  [DONE] Response received ({elapsed:.1f}s)")

    if response.status_code != 200:
        raise http_error(response, "Volcengine image generation")

    data = response.json()
    items = data.get("data") or []
    image_url = items[0].get("url") if items else None
    if not image_url:
        raise RuntimeError(f"Volcengine response missing image URL: {data}")

    path = resolve_output_path(prompt, output_dir, filename, ".jpeg")
    return download_image(image_url, path)


def generate(prompt: str, negative_prompt: str = None,
             aspect_ratio: str = "1:1", image_size: str = "1K",
             output_dir: str = None, filename: str = None,
             model: str = None, max_retries: int = MAX_RETRIES) -> str:
    """Generate an image with retries using the Volcengine backend."""
    access_key_id = os.environ.get("VOLCENGINE_ACCESS_KEY_ID") or os.environ.get("VOLCENGINE_AK")
    secret_access_key = os.environ.get("VOLCENGINE_SECRET_ACCESS_KEY") or os.environ.get("VOLCENGINE_SK")
    try:
        api_key = require_api_key(
            "VOLCENGINE_API_KEY",
            "ARK_API_KEY",
            message=(
                "No API key found. Set VOLCENGINE_API_KEY or ARK_API_KEY in the current environment or the project-root .env. "
                "If you only have AccessKeyID / SecretAccessKey, note that the current Seedream backend uses Ark API Key auth and does not yet sign AK/SK requests directly."
            ),
        )
    except ValueError:
        if access_key_id and secret_access_key:
            raise ValueError(
                "Detected VOLCENGINE AccessKeyID / SecretAccessKey, but the current Seedream backend requires VOLCENGINE_API_KEY or ARK_API_KEY."
            )
        raise
    base_url = os.environ.get("VOLCENGINE_BASE_URL") or DEFAULT_ENDPOINT
    resolved_model = model or os.environ.get("VOLCENGINE_MODEL") or DEFAULT_MODEL

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return _generate_image(
                api_key=api_key,
                prompt=prompt,
                negative_prompt=negative_prompt,
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                output_dir=output_dir,
                filename=filename,
                model=resolved_model,
                base_url=base_url,
            )
        except Exception as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            limited = is_rate_limit_error(exc)
            delay = retry_delay(attempt, rate_limited=limited)
            label = "Rate limit hit" if limited else f"Error: {exc}"
            print(f"\n  [WARN] {label}. Retrying in {delay}s...")
            time.sleep(delay)

    raise RuntimeError(f"Failed after {max_retries + 1} attempts. Last error: {last_error}")
