"""Mini-program facing endpoints — all require a valid bearer token."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from service.api.runtime import job_runner, workspace_manager
from service.auth.dependencies import get_current_user
from service.auth.users import UserRecord
from service.config import ServiceSettings, get_settings
from service.schemas.common import ResponseEnvelope
from service.schemas.projects import (
    ApproveConfirmationRequest,
    ApproveConfirmationResponse,
    ArtifactSummary,
    CancelJobResponse,
    ConfirmationSummary,
    CreateProjectRequest,
    CreateGenerationJobResponse,
    DeleteResponse,
    FinalizeSourcesResponse,
    GenerationJobSummary,
    JobEventSummary,
    ProjectSummary,
    QuotaSummary,
    SourceFileSummary,
    SourceUploadResponse,
    TemplateOption,
    UpdateProjectRequest,
    UrlSourceRequest,
)


router = APIRouter(prefix="/api/v1/mini/projects", tags=["mini-projects"])

# Allow-lists kept in sync with miniprogram/utils/format.js#inferSourceKind
_ALLOWED_SOURCE_KINDS = {
    "pdf", "docx", "pptx", "xlsx", "markdown", "html", "epub", "other",
}
_ALLOWED_SOURCE_ROLES = {"primary_source", "secondary_source", "reference"}


def _ensure_owner(project_id: str, user: UserRecord) -> None:
    try:
        workspace_manager().assert_owner(project_id, user.id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("", response_model=ResponseEnvelope[list[ProjectSummary]])
def list_projects(
    user: UserRecord = Depends(get_current_user),
) -> ResponseEnvelope[list[ProjectSummary]]:
    items = workspace_manager().list_user_projects(user.id)
    return ResponseEnvelope(data=items)


@router.post("", response_model=ResponseEnvelope[ProjectSummary])
def create_project(
    request: CreateProjectRequest,
    user: UserRecord = Depends(get_current_user),
) -> ResponseEnvelope[ProjectSummary]:
    project = workspace_manager().create_project(request, user_id=user.id)
    return ResponseEnvelope(data=project)


@router.get("/{project_id}", response_model=ResponseEnvelope[ProjectSummary])
def get_project(
    project_id: str,
    user: UserRecord = Depends(get_current_user),
) -> ResponseEnvelope[ProjectSummary]:
    _ensure_owner(project_id, user)
    project = workspace_manager().get_project(project_id)
    return ResponseEnvelope(data=project)


@router.patch("/{project_id}", response_model=ResponseEnvelope[ProjectSummary])
def update_project(
    project_id: str,
    request: UpdateProjectRequest,
    user: UserRecord = Depends(get_current_user),
) -> ResponseEnvelope[ProjectSummary]:
    _ensure_owner(project_id, user)
    try:
        return ResponseEnvelope(
            data=workspace_manager().update_project(project_id, request)
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/{project_id}", response_model=ResponseEnvelope[DeleteResponse])
def delete_project(
    project_id: str,
    user: UserRecord = Depends(get_current_user),
) -> ResponseEnvelope[DeleteResponse]:
    _ensure_owner(project_id, user)
    return ResponseEnvelope(data=workspace_manager().delete_project(project_id))


@router.get(
    "/{project_id}/sources",
    response_model=ResponseEnvelope[list[SourceFileSummary]],
)
def list_sources(
    project_id: str,
    user: UserRecord = Depends(get_current_user),
) -> ResponseEnvelope[list[SourceFileSummary]]:
    _ensure_owner(project_id, user)
    return ResponseEnvelope(data=workspace_manager().list_sources(project_id))


@router.post("/{project_id}/sources", response_model=ResponseEnvelope[SourceUploadResponse])
async def upload_source(
    project_id: str,
    file: UploadFile = File(...),
    source_kind: str = Form(...),
    role: str = Form(default="primary_source"),
    user: UserRecord = Depends(get_current_user),
    settings: ServiceSettings = Depends(get_settings),
) -> ResponseEnvelope[SourceUploadResponse]:
    _ensure_owner(project_id, user)

    if source_kind not in _ALLOWED_SOURCE_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported source_kind: {source_kind!r}; allowed: {sorted(_ALLOWED_SOURCE_KINDS)}",
        )
    if role not in _ALLOWED_SOURCE_ROLES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported role: {role!r}; allowed: {sorted(_ALLOWED_SOURCE_ROLES)}",
        )

    # Extension allow-list
    from pathlib import PurePosixPath
    filename = file.filename or "upload.bin"
    ext = PurePosixPath(filename).suffix.lower()
    allowed = settings.allowed_extensions_set
    if allowed and ext not in allowed:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported source extension: {ext or '(none)'}",
        )

    # Streaming size guard
    max_bytes = settings.max_upload_size_bytes
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Upload exceeds {max_bytes} bytes",
            )
        chunks.append(chunk)
    content = b"".join(chunks)

    uploaded = workspace_manager().register_source_upload(
        project_id=project_id,
        original_name=filename,
        content=content,
        source_kind=source_kind,
        role=role,
    )
    return ResponseEnvelope(data=uploaded)


@router.post(
    "/{project_id}/sources/finalize",
    response_model=ResponseEnvelope[FinalizeSourcesResponse],
)
def finalize_sources(
    project_id: str,
    user: UserRecord = Depends(get_current_user),
) -> ResponseEnvelope[FinalizeSourcesResponse]:
    _ensure_owner(project_id, user)
    try:
        result = workspace_manager().finalize_sources(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ResponseEnvelope(data=result)


@router.post(
    "/{project_id}/jobs/generate",
    response_model=ResponseEnvelope[CreateGenerationJobResponse],
)
def create_generation_job(
    project_id: str,
    user: UserRecord = Depends(get_current_user),
) -> ResponseEnvelope[CreateGenerationJobResponse]:
    _ensure_owner(project_id, user)
    try:
        result = workspace_manager().create_generation_job(project_id)
        if result.job_created:
            job_runner().enqueue_generation(project_id, result.job.job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ResponseEnvelope(data=result)


@router.get(
    "/{project_id}/jobs/latest",
    response_model=ResponseEnvelope[GenerationJobSummary],
)
def get_latest_job(
    project_id: str,
    user: UserRecord = Depends(get_current_user),
) -> ResponseEnvelope[GenerationJobSummary]:
    _ensure_owner(project_id, user)
    try:
        result = workspace_manager().get_latest_job(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ResponseEnvelope(data=result)


@router.get(
    "/{project_id}/artifacts",
    response_model=ResponseEnvelope[list[ArtifactSummary]],
)
def list_artifacts(
    project_id: str,
    user: UserRecord = Depends(get_current_user),
) -> ResponseEnvelope[list[ArtifactSummary]]:
    _ensure_owner(project_id, user)
    result = workspace_manager().list_artifacts(project_id)
    return ResponseEnvelope(data=result)


@router.get("/{project_id}/download/pptx")
def download_pptx(
    project_id: str,
    user: UserRecord = Depends(get_current_user),
) -> FileResponse:
    _ensure_owner(project_id, user)
    try:
        path = workspace_manager().resolve_primary_pptx_path(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path=path,
        filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


@router.delete(
    "/{project_id}/sources/{source_file_id}",
    response_model=ResponseEnvelope[DeleteResponse],
)
def delete_source(
    project_id: str,
    source_file_id: str,
    user: UserRecord = Depends(get_current_user),
) -> ResponseEnvelope[DeleteResponse]:
    _ensure_owner(project_id, user)
    try:
        return ResponseEnvelope(
            data=workspace_manager().delete_source(project_id, source_file_id)
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/{project_id}/sources/url",
    response_model=ResponseEnvelope[SourceUploadResponse],
)
def upload_source_from_url(
    project_id: str,
    request: UrlSourceRequest,
    user: UserRecord = Depends(get_current_user),
) -> ResponseEnvelope[SourceUploadResponse]:
    _ensure_owner(project_id, user)
    if request.role not in _ALLOWED_SOURCE_ROLES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported role: {request.role!r}; allowed: {sorted(_ALLOWED_SOURCE_ROLES)}",
        )
    try:
        return ResponseEnvelope(
            data=workspace_manager().register_url_source(
                project_id, request.url, request.role
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/{project_id}/confirmation",
    response_model=ResponseEnvelope[ConfirmationSummary],
)
def get_confirmation_for_user(
    project_id: str,
    user: UserRecord = Depends(get_current_user),
) -> ResponseEnvelope[ConfirmationSummary]:
    _ensure_owner(project_id, user)
    try:
        return ResponseEnvelope(
            data=workspace_manager().get_confirmation(project_id)
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/{project_id}/confirmation/approve",
    response_model=ResponseEnvelope[ApproveConfirmationResponse],
)
def approve_confirmation_for_user(
    project_id: str,
    request: ApproveConfirmationRequest,
    user: UserRecord = Depends(get_current_user),
) -> ResponseEnvelope[ApproveConfirmationResponse]:
    _ensure_owner(project_id, user)
    payload = request.model_copy(update={"approved_by": user.nickname or user.id})
    try:
        return ResponseEnvelope(
            data=workspace_manager().approve_confirmation(project_id, payload)
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/{project_id}/jobs",
    response_model=ResponseEnvelope[list[GenerationJobSummary]],
)
def list_jobs(
    project_id: str,
    user: UserRecord = Depends(get_current_user),
) -> ResponseEnvelope[list[GenerationJobSummary]]:
    _ensure_owner(project_id, user)
    return ResponseEnvelope(data=workspace_manager().list_jobs(project_id))


@router.get(
    "/{project_id}/jobs/{job_id}/events",
    response_model=ResponseEnvelope[list[JobEventSummary]],
)
def list_job_events(
    project_id: str,
    job_id: str,
    user: UserRecord = Depends(get_current_user),
) -> ResponseEnvelope[list[JobEventSummary]]:
    _ensure_owner(project_id, user)
    try:
        return ResponseEnvelope(
            data=workspace_manager().list_job_events(project_id, job_id)
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/{project_id}/jobs/{job_id}/cancel",
    response_model=ResponseEnvelope[CancelJobResponse],
)
def cancel_job(
    project_id: str,
    job_id: str,
    user: UserRecord = Depends(get_current_user),
) -> ResponseEnvelope[CancelJobResponse]:
    _ensure_owner(project_id, user)
    try:
        return ResponseEnvelope(
            data=workspace_manager().cancel_job(project_id, job_id)
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{project_id}/download/{artifact_id}")
def download_artifact(
    project_id: str,
    artifact_id: str,
    user: UserRecord = Depends(get_current_user),
) -> FileResponse:
    _ensure_owner(project_id, user)
    try:
        path, record = workspace_manager().get_artifact_path(project_id, artifact_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path=path,
        filename=record.file_name,
        media_type=record.content_type or "application/octet-stream",
    )


@router.get("/{project_id}/preview")
def preview_first_svg(
    project_id: str,
    user: UserRecord = Depends(get_current_user),
) -> FileResponse:
    _ensure_owner(project_id, user)
    try:
        path = workspace_manager().get_first_svg_path(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path=path, media_type="image/svg+xml", filename=path.name)


# ---------------------------------------------------------------------------
# Sibling routers (mounted under /api/v1/mini)
# ---------------------------------------------------------------------------

extras_router = APIRouter(prefix="/api/v1/mini", tags=["mini-extras"])


_TEMPLATES: list[TemplateOption] = [
    TemplateOption(canvas_format="ppt169", label="PPT 16:9 演示",
                   aspect_ratio="16:9", view_box="0 0 1280 720",
                   use_case="商务演示、会议汇报"),
    TemplateOption(canvas_format="ppt43", label="PPT 4:3 传统",
                   aspect_ratio="4:3", view_box="0 0 1024 768",
                   use_case="传统投影、学术演讲"),
    TemplateOption(canvas_format="xiaohongshu", label="小红书图文",
                   aspect_ratio="3:4", view_box="0 0 1242 1660",
                   use_case="图文分享、知识帖"),
    TemplateOption(canvas_format="square", label="朋友圈/IG 方图",
                   aspect_ratio="1:1", view_box="0 0 1080 1080",
                   use_case="正方海报、品牌展示"),
    TemplateOption(canvas_format="story", label="Story / 抖音竖屏",
                   aspect_ratio="9:16", view_box="0 0 1080 1920",
                   use_case="竖屏故事、短视频封面"),
    TemplateOption(canvas_format="banner169", label="Landscape Banner",
                   aspect_ratio="16:9", view_box="0 0 1920 1080",
                   use_case="网页 banner、数字屏"),
    TemplateOption(canvas_format="a4", label="A4 打印",
                   aspect_ratio="1:√2", view_box="0 0 1240 1754",
                   use_case="打印海报、单页传单"),
]


@extras_router.get(
    "/templates",
    response_model=ResponseEnvelope[list[TemplateOption]],
)
def list_templates() -> ResponseEnvelope[list[TemplateOption]]:
    return ResponseEnvelope(data=_TEMPLATES)


@extras_router.get("/quota", response_model=ResponseEnvelope[QuotaSummary])
def get_quota(
    user: UserRecord = Depends(get_current_user),
) -> ResponseEnvelope[QuotaSummary]:
    return ResponseEnvelope(data=workspace_manager().quota_for_user(user.id))
