from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from service.api.routes.admin import router as admin_router
from service.api.routes.auth import me_router, router as auth_router
from service.api.routes.projects import (
    extras_router,
    router as project_router,
)
from service.config import get_settings


_RATE_LIMITED_PATHS = {
    "/api/v1/auth/wechat/login": (10, 60.0),  # 10 requests / 60s per IP
    "/api/v1/mini/projects": (60, 60.0),       # generation triggers etc.
}


class _SimpleRateLimiter(BaseHTTPMiddleware):
    """In-memory sliding-window rate limiter for sensitive paths."""

    def __init__(self, app, rules: dict[str, tuple[int, float]]) -> None:
        super().__init__(app)
        self.rules = rules
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = Lock()

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Only enforce limits on POST to login + POST to /jobs/generate.
        rule = None
        if request.method == "POST" and path == "/api/v1/auth/wechat/login":
            rule = self.rules.get(path)
        elif request.method == "POST" and path.endswith("/jobs/generate"):
            rule = self.rules.get("/api/v1/mini/projects")

        if rule is not None:
            limit, window = rule
            ip = (request.client.host if request.client else "unknown")
            key = (path, ip)
            now = time.monotonic()
            with self._lock:
                bucket = self._hits[key]
                while bucket and now - bucket[0] > window:
                    bucket.popleft()
                if len(bucket) >= limit:
                    return JSONResponse(
                        status_code=429,
                        content={
                            "ok": False,
                            "error": {
                                "code": "rate_limited",
                                "message": f"Too many requests; max {limit} per {int(window)}s",
                            },
                        },
                    )
                bucket.append(now)

        return await call_next(request)


def _error_envelope(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "error": {"code": code, "message": message},
        },
    )


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="PPT Master Service", version="0.3.0")

    origins = [
        o.strip()
        for o in (settings.cors_allow_origins or "").split(",")
        if o.strip()
    ] or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )
    app.add_middleware(_SimpleRateLimiter, rules=_RATE_LIMITED_PATHS)

    @app.exception_handler(HTTPException)
    async def _http_exc_handler(_: Request, exc: HTTPException) -> JSONResponse:
        code_map = {
            400: "bad_request",
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            413: "payload_too_large",
            415: "unsupported_media_type",
            429: "rate_limited",
            503: "service_unavailable",
        }
        code = code_map.get(exc.status_code, "http_error")
        message = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return _error_envelope(exc.status_code, code, message)

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "ok": False,
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed",
                    "details": exc.errors(),
                },
            },
        )

    @app.get("/health")
    def healthcheck() -> dict[str, str]:
        return {
            "status": "ok",
            "workspace_root": str(settings.workspace_root),
        }

    app.include_router(auth_router)
    app.include_router(me_router)
    app.include_router(project_router)
    app.include_router(extras_router)
    app.include_router(admin_router)
    return app


app = create_app()