"""Entry point: ``python -m service`` boots the FastAPI app via uvicorn."""
from __future__ import annotations

import logging

import uvicorn

from service.config import get_settings


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def main() -> None:
    settings = get_settings()
    _configure_logging(settings.log_level)
    uvicorn.run(
        "service.api.app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
