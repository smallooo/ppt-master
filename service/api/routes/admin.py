from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status

from service.api.runtime import workspace_manager
from service.auth.users import UserRecord, list_users
from service.config import ServiceSettings, get_settings
from service.schemas.auth import AuthUserSummary
from service.schemas.common import ResponseEnvelope
from service.schemas.projects import (
    ApproveConfirmationRequest,
    ApproveConfirmationResponse,
    ConfirmationSummary,
    ProjectSummary,
    RejectConfirmationRequest,
)


def require_admin(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
    settings: ServiceSettings = Depends(get_settings),
) -> None:
    expected = settings.admin_token or ""
    if not expected:
        # Fail closed by default — admin endpoints are unusable until configured.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADMIN_TOKEN not configured",
        )
    if not x_admin_token or x_admin_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin token",
        )


router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


@router.get(
    "/projects/{project_id}/confirmation",
    response_model=ResponseEnvelope[ConfirmationSummary],
)
def get_confirmation(project_id: str) -> ResponseEnvelope[ConfirmationSummary]:
    try:
        summary = workspace_manager().get_confirmation(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Confirmation not found") from exc
    return ResponseEnvelope(data=summary)


@router.post(
    "/projects/{project_id}/confirmation/approve",
    response_model=ResponseEnvelope[ApproveConfirmationResponse],
)
def approve_confirmation(
    project_id: str,
    request: ApproveConfirmationRequest,
) -> ResponseEnvelope[ApproveConfirmationResponse]:
    try:
        result = workspace_manager().approve_confirmation(project_id, request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ResponseEnvelope(data=result)


@router.post(
    "/projects/{project_id}/confirmation/reject",
    response_model=ResponseEnvelope[ConfirmationSummary],
)
def reject_confirmation(
    project_id: str,
    request: RejectConfirmationRequest,
) -> ResponseEnvelope[ConfirmationSummary]:
    try:
        return ResponseEnvelope(
            data=workspace_manager().reject_confirmation(project_id, request)
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/projects", response_model=ResponseEnvelope[list[ProjectSummary]])
def admin_list_projects(limit: int = 200) -> ResponseEnvelope[list[ProjectSummary]]:
    return ResponseEnvelope(data=workspace_manager().admin_list_projects(limit=limit))


@router.get("/users", response_model=ResponseEnvelope[list[AuthUserSummary]])
def admin_list_users(limit: int = 200) -> ResponseEnvelope[list[AuthUserSummary]]:
    users: list[UserRecord] = list_users(limit=limit)
    return ResponseEnvelope(
        data=[
            AuthUserSummary(
                user_id=u.id,
                openid=u.openid,
                nickname=u.nickname,
                avatar_url=u.avatar_url,
            )
            for u in users
        ]
    )