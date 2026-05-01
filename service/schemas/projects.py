from __future__ import annotations

from datetime import datetime
from typing import List

from pydantic import BaseModel, Field

from service.models.enums import ConfirmationStatus, JobStatus, NextAction, ProjectStatus


class CreateProjectRequest(BaseModel):
    project_name: str = Field(min_length=1, max_length=120)
    canvas_format: str = Field(default="ppt169")
    requested_page_min: int = Field(default=8, ge=1)
    requested_page_max: int = Field(default=12, ge=1)
    source_type_hint: str | None = None
    biz_order_id: str | None = None


class ProjectSummary(BaseModel):
    project_id: str
    project_name: str
    canvas_format: str
    status: ProjectStatus
    status_text: str
    next_actions: List[NextAction]
    created_at: datetime
    updated_at: datetime


class SourceUploadResponse(BaseModel):
    source_file_id: str
    project_id: str
    original_name: str
    stored_name: str
    source_kind: str
    role: str
    size_bytes: int
    status: str


class FinalizeSourcesResponse(BaseModel):
    project_id: str
    status: ProjectStatus
    status_text: str
    normalized_source_count: int


class ConfirmationSummary(BaseModel):
    project_id: str
    status: ConfirmationStatus
    suggested_spec: dict[str, object]
    approved_spec: dict[str, object] | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    revision_note: str | None = None


class ApproveConfirmationRequest(BaseModel):
    approved_by: str = Field(min_length=1, max_length=120)
    revision_note: str | None = Field(default=None, max_length=2000)


class ApproveConfirmationResponse(BaseModel):
    project_id: str
    status: ProjectStatus
    status_text: str


class GenerationJobSummary(BaseModel):
    job_id: str
    project_id: str
    status: JobStatus
    current_stage: str
    progress_percent: int
    status_text: str
    created_at: datetime
    updated_at: datetime


class CreateGenerationJobResponse(BaseModel):
    project_id: str
    status: ProjectStatus
    status_text: str
    job: GenerationJobSummary
    job_created: bool = True


class ArtifactSummary(BaseModel):
    artifact_id: str
    artifact_type: str
    file_name: str
    storage_path: str
    content_type: str
    is_primary: bool
    status: str
    created_at: datetime


class SourceFileSummary(BaseModel):
    source_file_id: str
    project_id: str
    original_name: str
    stored_name: str
    source_kind: str
    role: str
    size_bytes: int
    status: str
    storage_path: str
    canonical_source_path: str | None = None
    normalized_markdown_path: str | None = None
    error_message: str | None = None
    created_at: datetime


class JobEventSummary(BaseModel):
    event_id: str
    job_id: str
    stage: str
    progress_percent: int
    message: str
    created_at: datetime


class UpdateProjectRequest(BaseModel):
    project_name: str | None = Field(default=None, min_length=1, max_length=120)
    requested_page_min: int | None = Field(default=None, ge=1)
    requested_page_max: int | None = Field(default=None, ge=1)
    source_type_hint: str | None = Field(default=None, max_length=32)


class UrlSourceRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    role: str = Field(default="primary_source", max_length=32)


class RejectConfirmationRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
    revision_note: str | None = Field(default=None, max_length=2000)


class TemplateOption(BaseModel):
    canvas_format: str
    label: str
    aspect_ratio: str
    view_box: str
    use_case: str


class QuotaSummary(BaseModel):
    user_id: str
    project_count: int
    project_quota: int | None = None
    storage_bytes: int
    storage_quota_bytes: int | None = None


class DeleteResponse(BaseModel):
    project_id: str
    deleted: bool


class CancelJobResponse(BaseModel):
    job_id: str
    project_id: str
    status: JobStatus
    status_text: str
