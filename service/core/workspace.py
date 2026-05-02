from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import shutil
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from service.adapters.openai_client import ChatMessage, OpenAIError, chat
from service.adapters.fallback_pptx_exporter import export_fallback_pptx
from service.adapters.source_normalizer import normalize_source_file
from service.config import ServiceSettings
from service.db import session_scope
from service.db import models as orm
from service.models.enums import ConfirmationStatus, JobStatus, NextAction, ProjectStatus
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
    RejectConfirmationRequest,
    SourceFileSummary,
    SourceUploadResponse,
    UpdateProjectRequest,
)
from service.storage.base import StorageBackend


PROJECT_SUBDIRS = (
    "incoming",
    "sources",
    "normalized",
    "images",
    "templates",
    "svg_output",
    "svg_final",
    "notes",
    "exports",
    "backup",
    "tmp",
    "manifests",
)


@dataclass
class ProjectRecord:
    project_id: str
    project_name: str
    canvas_format: str
    status: str
    status_text: str
    requested_page_min: int
    requested_page_max: int
    source_type_hint: str | None
    user_id: str | None
    biz_order_id: str | None
    created_at: str
    updated_at: str


@dataclass
class SourceFileRecord:
    source_file_id: str
    project_id: str
    original_name: str
    stored_name: str
    source_kind: str
    role: str
    size_bytes: int
    status: str
    storage_path: str
    canonical_source_path: str | None
    normalized_markdown_path: str | None
    error_message: str | None
    created_at: str


@dataclass
class ConfirmationRecord:
    project_id: str
    status: str
    suggested_spec: dict[str, object]
    approved_spec: dict[str, object] | None
    approved_by: str | None
    approved_at: str | None
    revision_note: str | None
    created_at: str
    updated_at: str


@dataclass
class GenerationJobRecord:
    job_id: str
    project_id: str
    status: str
    current_stage: str
    progress_percent: int
    status_text: str
    created_at: str
    updated_at: str


@dataclass
class ArtifactRecord:
    artifact_id: str
    project_id: str
    job_id: str
    artifact_type: str
    file_name: str
    storage_path: str
    content_type: str
    is_primary: bool
    status: str
    created_at: str


def _safe_join(root: Path, rel: str) -> Path:
    """Join *rel* to *root* and ensure the result stays inside *root*.

    Defensive guard against path-traversal via tampered ``storage_path`` values.
    """
    root_resolved = root.resolve()
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:  # pragma: no cover - defensive
        raise FileNotFoundError(f"Refusing path outside project root: {rel}") from exc
    return candidate


class WorkspaceManager:
    """Manage isolated per-project workspaces for the service wrapper."""

    def __init__(self, settings: ServiceSettings, storage: StorageBackend) -> None:
        self.settings = settings
        self.storage = storage

    def create_project(
        self,
        request: CreateProjectRequest,
        *,
        user_id: str | None = None,
    ) -> ProjectSummary:
        now = datetime.now(timezone.utc)
        project_id = str(uuid4())
        project_root = self.settings.projects_root / project_id

        self.storage.ensure_dir(self.settings.projects_root)
        for subdir in PROJECT_SUBDIRS:
            self.storage.ensure_dir(project_root / subdir)

        record = ProjectRecord(
            project_id=project_id,
            project_name=request.project_name,
            canvas_format=request.canvas_format,
            status=ProjectStatus.CREATED.value,
            status_text="项目已创建，请先上传源文件。",
            requested_page_min=request.requested_page_min,
            requested_page_max=request.requested_page_max,
            source_type_hint=request.source_type_hint,
            user_id=user_id,
            biz_order_id=request.biz_order_id,
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
        )
        self._write_project_record(project_root, record)
        return self._to_summary(record)

    def assert_owner(self, project_id: str, user_id: str) -> None:
        """Raise PermissionError if ``user_id`` does not own the project."""
        project_root = self.settings.projects_root / project_id
        record = self._read_project_record(project_root)
        if record.user_id is None:
            # legacy/anonymous projects: claim them on first authed access
            self._write_project_record(
                project_root,
                ProjectRecord(**{**asdict(record), "user_id": user_id}),
            )
            return
        if record.user_id != user_id:
            raise PermissionError("Project does not belong to current user")

    def get_project(self, project_id: str) -> ProjectSummary:
        project_root = self.settings.projects_root / project_id
        record = self._read_project_record(project_root)
        return self._to_summary(record)

    def list_user_projects(self, user_id: str) -> list[ProjectSummary]:
        """Return all projects owned by the given user, newest first."""
        with session_scope() as session:
            rows = (
                session.execute(
                    select(orm.Project)
                    .where(orm.Project.user_id == user_id)
                    .order_by(orm.Project.created_at.desc())
                )
                .scalars()
                .all()
            )
            return [self._to_summary(self._project_to_record(row)) for row in rows]

    def register_source_upload(
        self,
        project_id: str,
        original_name: str,
        content: bytes,
        source_kind: str,
        role: str,
    ) -> SourceUploadResponse:
        now = datetime.now(timezone.utc)
        project_root = self.settings.projects_root / project_id
        record = self._read_project_record(project_root)

        source_file_id = str(uuid4())
        safe_name = Path(original_name).name or f"upload_{source_file_id}"
        stored_name = f"{source_file_id}_{safe_name}"
        target_path = project_root / "incoming" / stored_name
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(content)

        source_record = SourceFileRecord(
            source_file_id=source_file_id,
            project_id=project_id,
            original_name=original_name,
            stored_name=stored_name,
            source_kind=source_kind,
            role=role,
            size_bytes=len(content),
            status="uploaded",
            storage_path=str(target_path.relative_to(project_root)),
            canonical_source_path=None,
            normalized_markdown_path=None,
            error_message=None,
            created_at=now.isoformat(),
        )
        source_records = self._read_source_records(project_root)
        source_records.append(source_record)
        self._write_source_records(project_root, source_records)

        updated = ProjectRecord(
            **{
                **asdict(record),
                "status": ProjectStatus.UPLOADING.value,
                "status_text": "Sources uploaded. Finalize uploads to continue.",
                "updated_at": now.isoformat(),
            }
        )
        self._write_project_record(project_root, updated)

        return SourceUploadResponse(
            source_file_id=source_record.source_file_id,
            project_id=project_id,
            original_name=source_record.original_name,
            stored_name=source_record.stored_name,
            source_kind=source_record.source_kind,
            role=source_record.role,
            size_bytes=source_record.size_bytes,
            status=source_record.status,
        )

    def finalize_sources(self, project_id: str) -> FinalizeSourcesResponse:
        now = datetime.now(timezone.utc)
        project_root = self.settings.projects_root / project_id
        record = self._read_project_record(project_root)
        source_records = self._read_source_records(project_root)

        artifacts = self._read_artifact_records(project_root)
        normalized_sources: list[SourceFileRecord] = []
        for source in source_records:
            incoming_path = project_root / source.storage_path
            canonical_name = source.stored_name
            canonical_path = project_root / "sources" / canonical_name
            canonical_path.parent.mkdir(parents=True, exist_ok=True)
            if incoming_path.resolve() != canonical_path.resolve():
                shutil.copy2(incoming_path, canonical_path)

            normalized_name = f"{Path(canonical_name).stem}.md"
            normalized_path = project_root / "normalized" / normalized_name
            normalized_path.parent.mkdir(parents=True, exist_ok=True)

            markdown = normalize_source_file(canonical_path, normalized_path)
            if not markdown:
                raise ValueError(f"Failed to normalize source: {source.original_name}")

            updated_source = SourceFileRecord(
                **{
                    **asdict(source),
                    "status": "normalized",
                    "canonical_source_path": str(canonical_path.relative_to(project_root)),
                    "normalized_markdown_path": str(normalized_path.relative_to(project_root)),
                    "error_message": None,
                }
            )
            normalized_sources.append(updated_source)
            artifacts.append(
                ArtifactRecord(
                    artifact_id=str(uuid4()),
                    project_id=project_id,
                    job_id="finalize_sources",
                    artifact_type="source_markdown",
                    file_name=normalized_path.name,
                    storage_path=str(normalized_path.relative_to(project_root)),
                    content_type="text/markdown",
                    is_primary=False,
                    status="active",
                    created_at=now.isoformat(),
                )
            )

        self._write_source_records(project_root, normalized_sources)
        self._write_artifact_records(project_root, artifacts)

        suggested_spec = {
            "project_name": record.project_name,
            "canvas_format": record.canvas_format,
            "page_range": {
                "min": record.requested_page_min,
                "max": record.requested_page_max,
            },
            "source_summary": self._build_source_summary(record, normalized_sources),
        }

        if normalized_sources:
            approved_spec = {
                **suggested_spec,
                "approved": True,
                "approved_by": "system",
                "approval_mode": "auto",
            }

            updated = ProjectRecord(
                **{
                    **asdict(record),
                    "status": ProjectStatus.READY_TO_GENERATE.value,
                    "status_text": "Sources normalized. Ready to start generation.",
                    "updated_at": now.isoformat(),
                }
            )
            self._write_project_record(project_root, updated)
            self._write_confirmation_record(
                project_root,
                ConfirmationRecord(
                    project_id=project_id,
                    status=ConfirmationStatus.APPROVED.value,
                    suggested_spec=approved_spec,
                    approved_spec=approved_spec,
                    approved_by="system",
                    approved_at=now.isoformat(),
                    revision_note=None,
                    created_at=now.isoformat(),
                    updated_at=now.isoformat(),
                ),
            )
            return FinalizeSourcesResponse(
                project_id=project_id,
                status=ProjectStatus.READY_TO_GENERATE,
                status_text=updated.status_text,
                normalized_source_count=len(normalized_sources),
            )

        outline_lines = self._build_default_source_bundle(record)
        outline_markdown = "\n".join(["# Normalized Sources", "", *outline_lines]).rstrip() + "\n"
        suggested_spec = {
            **suggested_spec,
            "outline_markdown": outline_markdown,
            "outline_sections": self._extract_outline_sections(outline_lines),
            "requires_user_confirmation": True,
            "generated_by": "system_default_outline",
            "confirmation_phase": "outline",
            "approval_mode": "user_required",
        }

        updated = ProjectRecord(
            **{
                **asdict(record),
                "status": ProjectStatus.AWAITING_CONFIRMATION.value,
                "status_text": "系统已生成默认提纲，请确认并修改后再开始生成。",
                "updated_at": now.isoformat(),
            }
        )
        self._write_project_record(project_root, updated)
        self._write_confirmation_record(
            project_root,
            ConfirmationRecord(
                project_id=project_id,
                status=ConfirmationStatus.PENDING.value,
                suggested_spec=suggested_spec,
                approved_spec=None,
                approved_by=None,
                approved_at=None,
                revision_note=None,
                created_at=now.isoformat(),
                updated_at=now.isoformat(),
            ),
        )
        return FinalizeSourcesResponse(
            project_id=project_id,
            status=ProjectStatus.AWAITING_CONFIRMATION,
            status_text=updated.status_text,
            normalized_source_count=len(normalized_sources),
        )

    def get_confirmation(self, project_id: str) -> ConfirmationSummary:
        project_root = self.settings.projects_root / project_id
        record = self._read_confirmation_record(project_root)
        return self._to_confirmation_summary(record)

    def approve_confirmation(
        self,
        project_id: str,
        request: ApproveConfirmationRequest,
    ) -> ApproveConfirmationResponse:
        now = datetime.now(timezone.utc)
        project_root = self.settings.projects_root / project_id
        project_record = self._read_project_record(project_root)
        if ProjectStatus(project_record.status) is not ProjectStatus.AWAITING_CONFIRMATION:
            raise ValueError("Project is not awaiting confirmation")

        confirmation = self._read_confirmation_record(project_root)
        suggested_spec = request.updated_spec or confirmation.suggested_spec
        outline_markdown = suggested_spec.get("outline_markdown") if isinstance(suggested_spec, dict) else None
        if isinstance(outline_markdown, str) and outline_markdown.strip():
            suggested_spec = {
                **suggested_spec,
                "outline_markdown": outline_markdown,
                "outline_sections": self._extract_outline_sections(outline_markdown.splitlines()),
            }
        confirmation_phase = suggested_spec.get("confirmation_phase") if isinstance(suggested_spec, dict) else None
        if confirmation_phase == "outline":
            next_spec = {
                **suggested_spec,
                "page_briefs": self._build_page_briefs(project_record, suggested_spec),
                "confirmation_phase": "page_briefs",
                "outline_confirmed_by": request.approved_by,
            }
            updated_confirmation = ConfirmationRecord(
                project_id=confirmation.project_id,
                status=ConfirmationStatus.PENDING.value,
                suggested_spec=next_spec,
                approved_spec=None,
                approved_by=None,
                approved_at=None,
                revision_note=request.revision_note,
                created_at=confirmation.created_at,
                updated_at=now.isoformat(),
            )
            self._write_confirmation_record(project_root, updated_confirmation)

            updated_project = ProjectRecord(
                **{
                    **asdict(project_record),
                    "status": ProjectStatus.AWAITING_CONFIRMATION.value,
                    "status_text": "已生成每页简要内容，请确认后再开始生成 PPT。",
                    "updated_at": now.isoformat(),
                }
            )
            self._write_project_record(project_root, updated_project)
            return ApproveConfirmationResponse(
                project_id=project_id,
                status=ProjectStatus.AWAITING_CONFIRMATION,
                status_text=updated_project.status_text,
            )

        approved_spec = {
            **suggested_spec,
            "approved": True,
            "approved_by": request.approved_by,
            "approval_mode": "user_confirmed",
            "confirmation_phase": "complete",
        }
        updated_confirmation = ConfirmationRecord(
            project_id=confirmation.project_id,
            status=ConfirmationStatus.APPROVED.value,
            suggested_spec=suggested_spec,
            approved_spec=approved_spec,
            approved_by=request.approved_by,
            approved_at=now.isoformat(),
            revision_note=request.revision_note,
            created_at=confirmation.created_at,
            updated_at=now.isoformat(),
        )
        self._write_confirmation_record(project_root, updated_confirmation)

        updated_project = ProjectRecord(
            **{
                **asdict(project_record),
                "status": ProjectStatus.READY_TO_GENERATE.value,
                "status_text": "Confirmation approved. Ready to start generation.",
                "updated_at": now.isoformat(),
            }
        )
        self._write_project_record(project_root, updated_project)
        return ApproveConfirmationResponse(
            project_id=project_id,
            status=ProjectStatus.READY_TO_GENERATE,
            status_text=updated_project.status_text,
        )

    def create_generation_job(self, project_id: str) -> CreateGenerationJobResponse:
        now = datetime.now(timezone.utc)
        project_root = self.settings.projects_root / project_id
        project_record = self._read_project_record(project_root)
        project_status = ProjectStatus(project_record.status)
        if project_status in {ProjectStatus.CREATED, ProjectStatus.UPLOADING}:
            self.finalize_sources(project_id)
            project_record = self._read_project_record(project_root)
            project_status = ProjectStatus(project_record.status)

        if project_status is ProjectStatus.AWAITING_CONFIRMATION:
            raise ValueError("Default outline requires confirmation before generation")

        if project_status is not ProjectStatus.READY_TO_GENERATE:
            raise ValueError("Project is not ready to generate")

        existing_job = self._try_read_latest_job(project_root)
        if existing_job and JobStatus(existing_job.status) in {JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRYING}:
            return CreateGenerationJobResponse(
                project_id=project_id,
                status=ProjectStatus(project_record.status),
                status_text=project_record.status_text,
                job=self._to_job_summary(existing_job),
                job_created=False,
            )

        job = GenerationJobRecord(
            job_id=str(uuid4()),
            project_id=project_id,
            status=JobStatus.QUEUED.value,
            current_stage="queued",
            progress_percent=0,
            status_text="Generation job queued.",
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
        )
        self._write_job_record(project_root, job)

        updated_project = ProjectRecord(
            **{
                **asdict(project_record),
                "status": ProjectStatus.GENERATING.value,
                "status_text": "Generation job queued.",
                "updated_at": now.isoformat(),
            }
        )
        self._write_project_record(project_root, updated_project)
        return CreateGenerationJobResponse(
            project_id=project_id,
            status=ProjectStatus.GENERATING,
            status_text=updated_project.status_text,
            job=self._to_job_summary(job),
            job_created=True,
        )

    def get_latest_job(self, project_id: str) -> GenerationJobSummary:
        project_root = self.settings.projects_root / project_id
        job = self._try_read_latest_job(project_root)
        if job is None:
            raise FileNotFoundError("No generation job found")
        return self._to_job_summary(job)

    def list_artifacts(self, project_id: str) -> list[ArtifactSummary]:
        project_root = self.settings.projects_root / project_id
        artifacts = self._read_artifact_records(project_root)
        return [self._to_artifact_summary(record) for record in artifacts]

    def mark_job_running(
        self,
        project_id: str,
        job_id: str,
        *,
        current_stage: str,
        progress_percent: int,
        status_text: str,
    ) -> GenerationJobSummary:
        now = datetime.now(timezone.utc)
        project_root = self.settings.projects_root / project_id
        job = self._require_job(project_root, job_id)
        updated_job = GenerationJobRecord(
            **{
                **asdict(job),
                "status": JobStatus.RUNNING.value,
                "current_stage": current_stage,
                "progress_percent": progress_percent,
                "status_text": status_text,
                "updated_at": now.isoformat(),
            }
        )
        self._write_job_record(project_root, updated_job)

        project = self._read_project_record(project_root)
        updated_project = ProjectRecord(
            **{
                **asdict(project),
                "status": ProjectStatus.GENERATING.value,
                "status_text": status_text,
                "updated_at": now.isoformat(),
            }
        )
        self._write_project_record(project_root, updated_project)
        return self._to_job_summary(updated_job)

    def materialize_generation_context(self, project_id: str, job_id: str) -> list[ArtifactRecord]:
        now = datetime.now(timezone.utc)
        project_root = self.settings.projects_root / project_id
        project = self._read_project_record(project_root)
        confirmation = self._read_confirmation_record(project_root)
        if confirmation.approved_spec is None:
            raise ValueError("Approved confirmation spec is missing")

        design_spec_path = project_root / "design_spec.md"
        spec_lock_path = project_root / "spec_lock.md"
        normalized_bundle_path = project_root / "normalized" / "normalized_sources.md"

        design_spec_content = self._build_design_spec_markdown(project, confirmation)
        spec_lock_content = self._build_spec_lock_markdown(project, confirmation)
        normalized_bundle_content = self._build_normalized_source_bundle(project_root)

        self.storage.write_text(design_spec_path, design_spec_content)
        self.storage.write_text(spec_lock_path, spec_lock_content)
        self.storage.write_text(normalized_bundle_path, normalized_bundle_content)

        artifacts = self._read_artifact_records(project_root)
        new_artifacts = [
            ArtifactRecord(
                artifact_id=str(uuid4()),
                project_id=project_id,
                job_id=job_id,
                artifact_type="normalized_bundle",
                file_name="normalized_sources.md",
                storage_path="normalized/normalized_sources.md",
                content_type="text/markdown",
                is_primary=False,
                status="active",
                created_at=now.isoformat(),
            ),
            ArtifactRecord(
                artifact_id=str(uuid4()),
                project_id=project_id,
                job_id=job_id,
                artifact_type="design_spec",
                file_name="design_spec.md",
                storage_path="design_spec.md",
                content_type="text/markdown",
                is_primary=False,
                status="active",
                created_at=now.isoformat(),
            ),
            ArtifactRecord(
                artifact_id=str(uuid4()),
                project_id=project_id,
                job_id=job_id,
                artifact_type="spec_lock",
                file_name="spec_lock.md",
                storage_path="spec_lock.md",
                content_type="text/markdown",
                is_primary=False,
                status="active",
                created_at=now.isoformat(),
            ),
        ]
        artifacts.extend(new_artifacts)
        self._write_artifact_records(project_root, artifacts)
        return new_artifacts

    def complete_generation_job(
        self,
        project_id: str,
        job_id: str,
        *,
        current_stage: str,
        progress_percent: int,
        status_text: str,
    ) -> GenerationJobSummary:
        now = datetime.now(timezone.utc)
        project_root = self.settings.projects_root / project_id
        job = self._require_job(project_root, job_id)
        updated_job = GenerationJobRecord(
            **{
                **asdict(job),
                "status": JobStatus.SUCCEEDED.value,
                "current_stage": current_stage,
                "progress_percent": progress_percent,
                "status_text": status_text,
                "updated_at": now.isoformat(),
            }
        )
        self._write_job_record(project_root, updated_job)

        project = self._read_project_record(project_root)
        updated_project = ProjectRecord(
            **{
                **asdict(project),
                "status": ProjectStatus.COMPLETED.value,
                "status_text": "Fallback PPTX export completed.",
                "updated_at": now.isoformat(),
            }
        )
        self._write_project_record(project_root, updated_project)
        return self._to_job_summary(updated_job)

    def fail_generation_job(self, project_id: str, job_id: str, error_message: str) -> GenerationJobSummary:
        now = datetime.now(timezone.utc)
        project_root = self.settings.projects_root / project_id
        job = self._require_job(project_root, job_id)
        updated_job = GenerationJobRecord(
            **{
                **asdict(job),
                "status": JobStatus.FAILED.value,
                "current_stage": "failed",
                "status_text": error_message,
                "updated_at": now.isoformat(),
            }
        )
        self._write_job_record(project_root, updated_job)

        project = self._read_project_record(project_root)
        updated_project = ProjectRecord(
            **{
                **asdict(project),
                "status": ProjectStatus.FAILED.value,
                "status_text": error_message,
                "updated_at": now.isoformat(),
            }
        )
        self._write_project_record(project_root, updated_project)
        return self._to_job_summary(updated_job)

    # ------------------------------------------------------------------
    # DB <-> dataclass mappers
    # ------------------------------------------------------------------

    @staticmethod
    def _project_to_record(row: orm.Project) -> ProjectRecord:
        return ProjectRecord(
            project_id=row.id,
            project_name=row.project_name,
            canvas_format=row.canvas_format,
            status=row.status,
            status_text=row.status_text,
            requested_page_min=row.requested_page_min,
            requested_page_max=row.requested_page_max,
            source_type_hint=row.source_type_hint,
            user_id=row.user_id,
            biz_order_id=row.biz_order_id,
            created_at=row.created_at.isoformat(),
            updated_at=row.updated_at.isoformat(),
        )

    @staticmethod
    def _source_to_record(row: orm.SourceFile) -> SourceFileRecord:
        return SourceFileRecord(
            source_file_id=row.id,
            project_id=row.project_id,
            original_name=row.original_name,
            stored_name=row.stored_name,
            source_kind=row.source_kind,
            role=row.role,
            size_bytes=row.size_bytes,
            status=row.status,
            storage_path=row.storage_path,
            canonical_source_path=row.canonical_source_path,
            normalized_markdown_path=row.normalized_markdown_path,
            error_message=row.error_message,
            created_at=row.created_at.isoformat(),
        )

    @staticmethod
    def _confirmation_to_record(row: orm.ConfirmationTask) -> ConfirmationRecord:
        return ConfirmationRecord(
            project_id=row.project_id,
            status=row.status,
            suggested_spec=row.suggested_spec or {},
            approved_spec=row.approved_spec,
            approved_by=row.approved_by,
            approved_at=row.approved_at.isoformat() if row.approved_at else None,
            revision_note=row.revision_note,
            created_at=row.created_at.isoformat(),
            updated_at=row.updated_at.isoformat(),
        )

    @staticmethod
    def _job_to_record(row: orm.GenerationJob) -> GenerationJobRecord:
        return GenerationJobRecord(
            job_id=row.id,
            project_id=row.project_id,
            status=row.status,
            current_stage=row.current_stage,
            progress_percent=row.progress_percent,
            status_text=row.status_text,
            created_at=row.created_at.isoformat(),
            updated_at=row.updated_at.isoformat(),
        )

    @staticmethod
    def _artifact_to_record(row: orm.Artifact) -> ArtifactRecord:
        return ArtifactRecord(
            artifact_id=row.id,
            project_id=row.project_id,
            job_id=row.job_id,
            artifact_type=row.artifact_type,
            file_name=row.file_name,
            storage_path=row.storage_path,
            content_type=row.content_type,
            is_primary=row.is_primary,
            status=row.status,
            created_at=row.created_at.isoformat(),
        )

    # ------------------------------------------------------------------
    # DB-backed read/write helpers (keep prior signatures used by methods)
    # ------------------------------------------------------------------

    @staticmethod
    def _project_id_from_root(project_root: Path) -> str:
        return project_root.name

    def _read_project_record(self, project_root: Path) -> ProjectRecord:
        project_id = self._project_id_from_root(project_root)
        with session_scope() as session:
            row = session.get(orm.Project, project_id)
            if row is None:
                raise FileNotFoundError(f"Project {project_id} not found")
            return self._project_to_record(row)

    def _read_source_records(self, project_root: Path) -> list[SourceFileRecord]:
        project_id = self._project_id_from_root(project_root)
        with session_scope() as session:
            rows = (
                session.execute(
                    select(orm.SourceFile)
                    .where(orm.SourceFile.project_id == project_id)
                    .order_by(orm.SourceFile.created_at)
                )
                .scalars()
                .all()
            )
            return [self._source_to_record(r) for r in rows]

    def _read_confirmation_record(self, project_root: Path) -> ConfirmationRecord:
        project_id = self._project_id_from_root(project_root)
        with session_scope() as session:
            row = session.execute(
                select(orm.ConfirmationTask).where(
                    orm.ConfirmationTask.project_id == project_id
                )
            ).scalar_one_or_none()
            if row is None:
                raise FileNotFoundError(
                    f"Confirmation task not found for project {project_id}"
                )
            return self._confirmation_to_record(row)

    def _read_artifact_records(self, project_root: Path) -> list[ArtifactRecord]:
        project_id = self._project_id_from_root(project_root)
        with session_scope() as session:
            rows = (
                session.execute(
                    select(orm.Artifact)
                    .where(orm.Artifact.project_id == project_id)
                    .order_by(orm.Artifact.created_at)
                )
                .scalars()
                .all()
            )
            return [self._artifact_to_record(r) for r in rows]

    def resolve_primary_pptx_path(self, project_id: str) -> Path:
        project_root = self.settings.projects_root / project_id
        with session_scope() as session:
            row = session.execute(
                select(orm.Artifact)
                .where(
                    orm.Artifact.project_id == project_id,
                    orm.Artifact.artifact_type == "pptx_main",
                    orm.Artifact.is_primary.is_(True),
                    orm.Artifact.status == "active",
                )
                .order_by(orm.Artifact.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                raise FileNotFoundError("Primary PPTX artifact not found")
            return _safe_join(project_root, row.storage_path)

    def _write_confirmation_record(
        self, project_root: Path, record: ConfirmationRecord
    ) -> None:
        with session_scope() as session:
            row = session.execute(
                select(orm.ConfirmationTask).where(
                    orm.ConfirmationTask.project_id == record.project_id
                )
            ).scalar_one_or_none()
            if row is None:
                row = orm.ConfirmationTask(project_id=record.project_id)
                session.add(row)
            row.status = record.status
            row.suggested_spec = record.suggested_spec
            row.approved_spec = record.approved_spec
            row.approved_by = record.approved_by
            row.approved_at = (
                datetime.fromisoformat(record.approved_at)
                if record.approved_at
                else None
            )
            row.revision_note = record.revision_note

    def _try_read_latest_job(self, project_root: Path) -> GenerationJobRecord | None:
        project_id = self._project_id_from_root(project_root)
        with session_scope() as session:
            row = session.execute(
                select(orm.GenerationJob)
                .where(orm.GenerationJob.project_id == project_id)
                .order_by(orm.GenerationJob.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            return self._job_to_record(row) if row else None

    def _require_job(self, project_root: Path, job_id: str) -> GenerationJobRecord:
        with session_scope() as session:
            row = session.get(orm.GenerationJob, job_id)
            if row is None or row.project_id != self._project_id_from_root(project_root):
                raise FileNotFoundError("Generation job not found")
            return self._job_to_record(row)

    def _write_job_record(self, project_root: Path, record: GenerationJobRecord) -> None:
        with session_scope() as session:
            row = session.get(orm.GenerationJob, record.job_id)
            if row is None:
                row = orm.GenerationJob(
                    id=record.job_id, project_id=record.project_id
                )
                session.add(row)
            row.project_id = record.project_id
            row.status = record.status
            row.current_stage = record.current_stage
            row.progress_percent = record.progress_percent
            row.status_text = record.status_text

    def _write_artifact_records(
        self, project_root: Path, records: list[ArtifactRecord]
    ) -> None:
        # Upsert by id; deletions are not expected (we mark superseded).
        with session_scope() as session:
            for rec in records:
                row = session.get(orm.Artifact, rec.artifact_id)
                if row is None:
                    row = orm.Artifact(id=rec.artifact_id)
                    session.add(row)
                row.project_id = rec.project_id
                row.job_id = rec.job_id
                row.artifact_type = rec.artifact_type
                row.file_name = rec.file_name
                row.storage_path = rec.storage_path
                row.content_type = rec.content_type
                row.is_primary = rec.is_primary
                row.status = rec.status

    def _write_source_records(
        self, project_root: Path, records: list[SourceFileRecord]
    ) -> None:
        with session_scope() as session:
            for rec in records:
                row = session.get(orm.SourceFile, rec.source_file_id)
                if row is None:
                    row = orm.SourceFile(id=rec.source_file_id)
                    session.add(row)
                row.project_id = rec.project_id
                row.original_name = rec.original_name
                row.stored_name = rec.stored_name
                row.source_kind = rec.source_kind
                row.role = rec.role
                row.size_bytes = rec.size_bytes
                row.status = rec.status
                row.storage_path = rec.storage_path
                row.canonical_source_path = rec.canonical_source_path
                row.normalized_markdown_path = rec.normalized_markdown_path
                row.error_message = rec.error_message

    def _write_project_record(self, project_root: Path, record: ProjectRecord) -> None:
        with session_scope() as session:
            row = session.get(orm.Project, record.project_id)
            if row is None:
                row = orm.Project(id=record.project_id)
                session.add(row)
            row.user_id = record.user_id
            row.project_name = record.project_name
            row.canvas_format = record.canvas_format
            row.status = record.status
            row.status_text = record.status_text
            row.requested_page_min = record.requested_page_min
            row.requested_page_max = record.requested_page_max
            row.source_type_hint = record.source_type_hint
            row.biz_order_id = record.biz_order_id

    def _to_summary(self, record: ProjectRecord) -> ProjectSummary:
        next_actions = self._next_actions(ProjectStatus(record.status))
        return ProjectSummary(
            project_id=record.project_id,
            project_name=record.project_name,
            canvas_format=record.canvas_format,
            status=ProjectStatus(record.status),
            status_text=record.status_text,
            next_actions=next_actions,
            created_at=datetime.fromisoformat(record.created_at),
            updated_at=datetime.fromisoformat(record.updated_at),
        )

    def _to_confirmation_summary(self, record: ConfirmationRecord) -> ConfirmationSummary:
        approved_at = datetime.fromisoformat(record.approved_at) if record.approved_at else None
        return ConfirmationSummary(
            project_id=record.project_id,
            status=ConfirmationStatus(record.status),
            suggested_spec=record.suggested_spec,
            approved_spec=record.approved_spec,
            approved_by=record.approved_by,
            approved_at=approved_at,
            revision_note=record.revision_note,
        )

    def _to_job_summary(self, record: GenerationJobRecord) -> GenerationJobSummary:
        return GenerationJobSummary(
            job_id=record.job_id,
            project_id=record.project_id,
            status=JobStatus(record.status),
            current_stage=record.current_stage,
            progress_percent=record.progress_percent,
            status_text=record.status_text,
            created_at=datetime.fromisoformat(record.created_at),
            updated_at=datetime.fromisoformat(record.updated_at),
        )

    def _to_artifact_summary(self, record: ArtifactRecord) -> ArtifactSummary:
        return ArtifactSummary(
            artifact_id=record.artifact_id,
            artifact_type=record.artifact_type,
            file_name=record.file_name,
            storage_path=record.storage_path,
            content_type=record.content_type,
            is_primary=record.is_primary,
            status=record.status,
            created_at=datetime.fromisoformat(record.created_at),
        )

    def _build_design_spec_markdown(
        self,
        project: ProjectRecord,
        confirmation: ConfirmationRecord,
    ) -> str:
        spec = confirmation.approved_spec or confirmation.suggested_spec
        source_summary = spec.get("source_summary", [])
        image_resources = self._build_default_image_resources(project)
        design_profile = self._build_design_profile(project)
        page_rhythm = self._build_page_rhythm(project)
        lines = [
            f"# {project.project_name}",
            "",
            "## I. Project Information",
            "",
            f"- Project name: {project.project_name}",
            f"- Topic hint: {project.source_type_hint or project.project_name}",
            f"- Target audience: {design_profile['target_audience']}",
            f"- Style objective: {design_profile['style_objective']}",
            "",
            "## II. Canvas & Page Plan",
            "",
            f"- Canvas format: {project.canvas_format}",
            f"- Canvas dimensions: {design_profile['canvas_dimensions']}",
            f"- Requested pages: {project.requested_page_min}-{project.requested_page_max}",
            f"- Approved by: {confirmation.approved_by or 'unknown'}",
            "",
            "## III. Visual Theme",
            "",
            f"- Theme statement: {design_profile['theme_statement']}",
            f"- Primary color: {design_profile['colors']['primary']}",
            f"- Secondary color: {design_profile['colors']['secondary']}",
            f"- Accent color: {design_profile['colors']['accent']}",
            f"- Background color: {design_profile['colors']['background']}",
            f"- Surface color: {design_profile['colors']['surface']}",
            f"- Primary text color: {design_profile['colors']['text_primary']}",
            f"- Secondary text color: {design_profile['colors']['text_secondary']}",
            "",
            "## IV. Typography Plan",
            "",
            f"- Title font: {design_profile['typography']['title_family']}",
            f"- Body font: {design_profile['typography']['body_family']}",
            f"- Emphasis font: {design_profile['typography']['emphasis_family']}",
            f"- Code font: {design_profile['typography']['code_family']}",
            f"- Body baseline: {design_profile['typography']['body']}px",
            f"- Title size: {design_profile['typography']['title']}px",
            f"- Subtitle size: {design_profile['typography']['subtitle']}px",
            f"- Annotation size: {design_profile['typography']['annotation']}px",
            "",
            "## V. Layout System",
            "",
            f"- Cover strategy: {design_profile['layout']['cover']}",
            f"- Content strategy: {design_profile['layout']['content']}",
            f"- Dense-page strategy: {design_profile['layout']['dense']}",
            f"- Breathing-page strategy: {design_profile['layout']['breathing']}",
            "",
            "### Page Rhythm Plan",
            "",
        ]
        for page_key, rhythm in page_rhythm.items():
            lines.append(f"- {page_key}: {rhythm}")
        lines.extend([
            "",
            "## VI. Icon System",
            "",
            f"- Icon library: {design_profile['icons']['library']}",
            f"- Stroke width: {design_profile['icons']['stroke_width']}",
            f"- Inventory: {', '.join(design_profile['icons']['inventory'])}",
            "",
            "## VII. Visualization Reference List",
            "",
            f"- Preferred visual forms: {', '.join(design_profile['visualization_preferences'])}",
            "",
            "## VIII. Page Brief Draft",
            "",
        ])
        for brief in spec.get("page_briefs", []):
            if not isinstance(brief, dict):
                continue
            lines.append(
                f"- P{int(brief.get('page_no') or 0):02d} {brief.get('title', 'Untitled')}: {brief.get('summary', '')}"
            )
            for bullet in brief.get("bullets", [])[:4]:
                lines.append(f"  - {bullet}")
        lines.extend([
            "",
            "## Source Summary",
            "",
        ])
        if source_summary:
            for source in source_summary:
                lines.append(
                    f"- {source.get('original_name', 'unknown')} "
                    f"({source.get('source_kind', 'unknown')}, role={source.get('role', 'unknown')})"
                )
        else:
            lines.append("- No sources recorded")
        lines.extend([
            "",
            "## Approved Spec Payload",
            "",
            "```json",
            json.dumps(spec, ensure_ascii=False, indent=2),
            "```",
            "",
            "## IX. Image Resource List",
            "",
            "| Filename | Dimensions | Ratio | Purpose | Intent | Type | Status | Generation Description |",
            "| -------- | ---------- | ----- | ------- | ------ | ---- | ------ | ---------------------- |",
        ])
        for image in image_resources:
            lines.append(
                "| "
                f"{image['filename']} | {image['dimensions']} | {image['ratio']} | "
                f"{image['purpose']} | {image['intent']} | {image['type']} | "
                f"{image['status']} | {image['description']} |"
            )
        lines.extend([
            "",
            "## X. Content Outline",
            "",
            "- P01 Cover: full-bleed opening with one strong message and restrained overlay.",
            "- Early pages should alternate between overview, evidence, and framework pages rather than repeating one split layout.",
            "- Dense pages should handle comparisons, lists, tables, and structured arguments.",
            "- Breathing pages should use a single hero statement, metric, quote, or image-led insight.",
            "- Final pages should converge to recommendation, roadmap, and close.",
            "",
            "## XI. Speaker Notes Requirements",
            "",
            "- Each page should have a short presenter script with a transition sentence.",
            "- Notes should explain why the page exists, not restate all visible text.",
            "",
            "## XII. Technical Constraints Reminder",
            "",
            "- Use native SVG elements compatible with PPT export.",
            "- Avoid repeating the same composition family more than twice in a row.",
            "- Use only approved project-local images and icon inventory entries.",
            "",
        ])
        return "\n".join(lines)

    def _build_spec_lock_markdown(
        self,
        project: ProjectRecord,
        confirmation: ConfirmationRecord,
    ) -> str:
        spec = confirmation.approved_spec or confirmation.suggested_spec
        design_profile = self._build_design_profile(project)
        lock_payload = {
            "project_id": project.project_id,
            "project_name": project.project_name,
            "canvas_format": project.canvas_format,
            "approved_by": confirmation.approved_by,
            "approved_at": confirmation.approved_at,
            "colors": design_profile["colors"],
            "typography": design_profile["typography"],
            "icons": design_profile["icons"],
            "images": self._build_default_image_resources(project),
            "page_rhythm": self._build_page_rhythm(project),
            "layout": design_profile["layout"],
            "visualization_preferences": design_profile["visualization_preferences"],
            "spec": spec,
        }
        return "\n".join(
            [
                "# Spec Lock",
                "",
                "```json",
                json.dumps(lock_payload, ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        )

    def _build_normalized_source_bundle(self, project_root: Path) -> str:
        project = self._read_project_record(project_root)
        sources = self._read_source_records(project_root)
        sections: list[str] = ["# Normalized Sources", ""]
        for source in sources:
            if not source.normalized_markdown_path:
                continue
            normalized_path = project_root / source.normalized_markdown_path
            content = normalized_path.read_text(encoding="utf-8", errors="replace").strip()
            sections.extend(
                [
                    f"## {source.original_name}",
                    "",
                    f"- Source kind: {source.source_kind}",
                    f"- Role: {source.role}",
                    "",
                    content or "_Empty normalized content._",
                    "",
                ]
            )
        if len(sections) == 2:
            outline_markdown = self._resolve_outline_markdown(project_root)
            if outline_markdown:
                return outline_markdown.rstrip() + "\n"
            sections.extend(self._build_default_source_bundle(project))
        return "\n".join(sections).rstrip() + "\n"

    def _build_source_summary(
        self,
        project: ProjectRecord,
        normalized_sources: list[SourceFileRecord],
    ) -> list[dict[str, object]]:
        if normalized_sources:
            return [
                {
                    "source_file_id": source.source_file_id,
                    "original_name": source.original_name,
                    "source_kind": source.source_kind,
                    "role": source.role,
                    "normalized_markdown_path": source.normalized_markdown_path,
                }
                for source in normalized_sources
            ]

        return [
            {
                "source_file_id": "system-generated",
                "original_name": "系统默认提纲",
                "source_kind": "generated",
                "role": "system_seed",
                "normalized_markdown_path": "normalized/normalized_sources.md",
                "description": "No source files uploaded. Generated a default outline from project metadata.",
            }
        ]

    def _build_default_image_resources(self, project: ProjectRecord) -> list[dict[str, str]]:
        dimensions, ratio = self._canvas_dimensions(project.canvas_format)
        topic_hint = project.source_type_hint or "presentation"
        design_profile = self._build_design_profile(project)
        return [
            {
                "filename": "cover_bg.png",
                "dimensions": dimensions,
                "ratio": ratio,
                "purpose": "封面背景图",
                "intent": "Hero",
                "type": "Background",
                "status": "Pending",
                "description": (
                    f"为《{project.project_name}》生成一张{design_profile['style_objective']}风格的专业演示文稿封面背景图，"
                    f"主题围绕 {topic_hint}，主色使用 {design_profile['colors']['primary']} 与 {design_profile['colors']['accent']}，"
                    "保留大面积留白供标题覆盖，并避免通用企业模板感。"
                ),
            }
        ]

    def _build_design_profile(self, project: ProjectRecord) -> dict[str, object]:
        hint = f"{project.project_name} {project.source_type_hint or ''}".lower()
        if any(token in hint for token in ("ai", "agent", "tech", "digital", "saas", "互联网", "科技")):
            return {
                "style_objective": "科技咨询",
                "target_audience": "管理层、产品团队、技术决策者",
                "theme_statement": "结构化叙事下的科技感表达，强调结论、层次与视觉留白。",
                "canvas_dimensions": self._canvas_dimensions(project.canvas_format)[0],
                "colors": {
                    "primary": "#0F3D66",
                    "secondary": "#DCE7F2",
                    "accent": "#18B8A8",
                    "background": "#F6F8FB",
                    "surface": "#FFFFFF",
                    "text_primary": "#10253E",
                    "text_secondary": "#4F647A",
                },
                "typography": {
                    "title_family": '"Microsoft YaHei", "PingFang SC", sans-serif',
                    "body_family": '"Microsoft YaHei", "PingFang SC", sans-serif',
                    "emphasis_family": 'Arial, "Microsoft YaHei", sans-serif',
                    "code_family": 'Consolas, "Courier New", monospace',
                    "body": 20,
                    "title": 40,
                    "subtitle": 28,
                    "annotation": 14,
                },
                "icons": {
                    "library": "chunk-filled",
                    "stroke_width": 2,
                    "inventory": [
                        "chunk-filled/chart-bar",
                        "chunk-filled/users",
                        "chunk-filled/target",
                        "chunk-filled/arrow-trend-up",
                    ],
                },
                "layout": {
                    "cover": "Full-bleed hero visual with restrained overlay and centered or offset title block.",
                    "content": "Alternate between asymmetric editorial layouts, split layouts, and evidence-led comparison pages.",
                    "dense": "Use structured comparison tables, KPI bands, timelines, and grouped evidence blocks.",
                    "breathing": "Use one hero statement, one dominant chart, or one image-led insight per page.",
                },
                "visualization_preferences": ["kpi_band", "comparison_matrix", "timeline", "grouped_bar_chart"],
            }

        return {
            "style_objective": "高端通用",
            "target_audience": "客户、管理层、跨部门汇报对象",
            "theme_statement": "以清晰逻辑为主线，通过节奏变化与重点放大减少模板感。",
            "canvas_dimensions": self._canvas_dimensions(project.canvas_format)[0],
            "colors": {
                "primary": "#1F4E79",
                "secondary": "#E8EEF5",
                "accent": "#D97841",
                "background": "#FAFBFD",
                "surface": "#FFFFFF",
                "text_primary": "#1F2937",
                "text_secondary": "#5B6472",
            },
            "typography": {
                "title_family": '"Microsoft YaHei", "PingFang SC", sans-serif',
                "body_family": '"Microsoft YaHei", "PingFang SC", sans-serif',
                "emphasis_family": 'Georgia, "Times New Roman", serif',
                "code_family": 'Consolas, "Courier New", monospace',
                "body": 18,
                "title": 36,
                "subtitle": 26,
                "annotation": 13,
            },
            "icons": {
                "library": "tabler-filled",
                "stroke_width": 2,
                "inventory": [
                    "tabler-filled/chart-bar",
                    "tabler-filled/target",
                    "tabler-filled/bulb",
                    "tabler-filled/message",
                ],
            },
            "layout": {
                "cover": "Hero cover with strong title hierarchy and one visual anchor.",
                "content": "Mix editorial text-led pages, side-by-side pages, and framework pages rather than repeating one grid.",
                "dense": "Allow 2-column or 3-block structures for analysis and comparison pages.",
                "breathing": "Reserve whitespace and avoid multi-card grids on insight or transition pages.",
            },
            "visualization_preferences": ["process_flow", "comparison_table", "timeline", "kpi_cards"],
        }

    def _build_page_rhythm(self, project: ProjectRecord) -> dict[str, str]:
        max_pages = max(project.requested_page_min, project.requested_page_max)
        plan: dict[str, str] = {}
        for page_no in range(1, max_pages + 1):
            key = f"P{page_no:02d}"
            if page_no == 1 or page_no == max_pages:
                plan[key] = "anchor"
            elif page_no in {2, 5, 8}:
                plan[key] = "breathing"
            else:
                plan[key] = "dense"
        return plan

    def _canvas_dimensions(self, canvas_format: str) -> tuple[str, str]:
        presets = {
            "ppt169": ("1920x1080", "16:9"),
            "ppt43": ("1600x1200", "4:3"),
            "story": ("1080x1920", "9:16"),
            "xhs": ("1242x1660", "3:4"),
        }
        return presets.get((canvas_format or "").lower(), ("1920x1080", "16:9"))

    def _build_default_source_bundle(self, project: ProjectRecord) -> list[str]:
        topic = (project.source_type_hint or project.project_name or "该主题").strip()
        min_pages = project.requested_page_min
        max_pages = project.requested_page_max
        lines = [
            "## 项目概览",
            "",
            f"- 项目名称：{project.project_name}",
            f"- 主题方向：{topic}",
            f"- 建议页数：{min_pages}-{max_pages} 页",
            f"- 输出画布：{project.canvas_format}",
            "",
            f"本演示文稿围绕“{topic}”展开，适用于在缺少原始素材时快速生成一版可编辑的初稿。",
            "建议先用这版初稿确认结构与重点，再补充真实素材进行增强。",
            "",
            "## 背景与目标",
            "",
            f"- 说明“{topic}”相关背景、当前现状与关键问题。",
            "- 阐明本次汇报希望解决的核心目标、受众关注点与预期结果。",
            "- 定义成功标准，便于后续内容和行动建议对齐。",
            "",
            "## 核心信息框架",
            "",
            "- 提炼 3 到 5 个最重要的信息点，避免平均用力。",
            "- 将内容组织为“现状判断 - 关键洞察 - 解决方案 - 落地动作”的叙事结构。",
            "- 每一页聚焦一个中心结论，并配套必要的解释和证据。",
            "",
            "## 方案与亮点",
            "",
            f"- 给出围绕“{topic}”的总体方案、关键模块或执行路径。",
            "- 强调差异化亮点、可复用能力与预期价值。",
            "- 如有必要，可补充时间线、职责分工或资源需求。",
            "",
            "## 风险与下一步",
            "",
            "- 识别推进过程中可能遇到的主要风险、依赖项和约束条件。",
            "- 给出分阶段推进建议，明确近期动作与里程碑。",
            "- 在结尾总结核心结论，并给出下一步决策建议。",
            "",
        ]
        return lines

    def _build_page_briefs(
        self,
        project: ProjectRecord,
        spec: dict[str, object],
    ) -> list[dict[str, object]]:
        outline_markdown = str(spec.get("outline_markdown") or "").strip()
        if not outline_markdown or not self.settings.openai_api_key:
            return self._fallback_page_briefs(project, spec)

        user_prompt = (
            f"PROJECT: {project.project_name}\n"
            f"PAGE RANGE: {project.requested_page_min}-{project.requested_page_max}\n\n"
            "OUTLINE MARKDOWN:\n"
            f"{outline_markdown[:12000]}\n\n"
            "Return strict JSON with key 'pages'. Each page item must include page_no, title, summary, and bullets. "
            "Keep it concise and suitable for a user review step before final PPT generation."
        )
        try:
            raw = chat(
                self.settings,
                model=self.settings.openai_model_strategist,
                messages=[
                    ChatMessage(
                        "system",
                        "You draft page-by-page PPT review briefs. Reply ONLY strict JSON: "
                        '{"pages":[{"page_no":1,"title":"...","summary":"...","bullets":["...","..."]}]}'
                    ),
                    ChatMessage("user", user_prompt),
                ],
                temperature=0.3,
                response_format={"type": "json_object"},
            )
            payload = json.loads(raw)
            pages = payload.get("pages")
            if isinstance(pages, list):
                cleaned: list[dict[str, object]] = []
                for idx, item in enumerate(pages, start=1):
                    if not isinstance(item, dict):
                        continue
                    bullets = [str(v).strip() for v in (item.get("bullets") or []) if str(v).strip()]
                    cleaned.append(
                        {
                            "page_no": int(item.get("page_no") or idx),
                            "title": str(item.get("title") or f"第 {idx} 页").strip(),
                            "summary": str(item.get("summary") or "").strip(),
                            "bullets": bullets[:4],
                        }
                    )
                if cleaned:
                    return cleaned
        except (OpenAIError, json.JSONDecodeError, TypeError, ValueError):
            pass
        return self._fallback_page_briefs(project, spec)

    def _fallback_page_briefs(
        self,
        project: ProjectRecord,
        spec: dict[str, object],
    ) -> list[dict[str, object]]:
        sections = [str(v).strip() for v in (spec.get("outline_sections") or []) if str(v).strip()]
        if not sections:
            sections = ["项目概览", "背景与目标", "核心信息框架", "方案与亮点", "风险与下一步"]
        target_count = max(project.requested_page_min, min(project.requested_page_max, len(sections)))
        briefs: list[dict[str, object]] = []
        for idx, section in enumerate(sections[:target_count], start=1):
            briefs.append(
                {
                    "page_no": idx,
                    "title": section,
                    "summary": f"围绕“{section}”提炼这一页的核心结论与表达重点。",
                    "bullets": [
                        f"说明 {section} 的关键背景或当前判断。",
                        f"提炼与 {section} 直接相关的 2-3 个重点信息。",
                        "给出这一页希望让观众记住的结论。",
                    ],
                }
            )
        return briefs

    def _resolve_outline_markdown(self, project_root: Path) -> str | None:
        try:
            confirmation = self._read_confirmation_record(project_root)
        except FileNotFoundError:
            return None
        spec = confirmation.approved_spec or confirmation.suggested_spec
        outline_markdown = spec.get("outline_markdown") if isinstance(spec, dict) else None
        if isinstance(outline_markdown, str) and outline_markdown.strip():
            return outline_markdown
        return None

    def _extract_outline_sections(self, lines: list[str]) -> list[str]:
        sections: list[str] = []
        for raw_line in lines:
            line = raw_line.strip()
            if line.startswith("## "):
                sections.append(line[3:].strip())
        return sections

    def export_fallback_pptx_artifact(self, project_id: str, job_id: str) -> ArtifactRecord:
        now = datetime.now(timezone.utc)
        project_root = self.settings.projects_root / project_id
        project = self._read_project_record(project_root)
        normalized_bundle_path = project_root / "normalized" / "normalized_sources.md"
        if not normalized_bundle_path.exists():
            raise FileNotFoundError("Normalized bundle artifact not found")

        output_path = project_root / "exports" / f"{project.project_name}_fallback.pptx"
        export_fallback_pptx(
            project_name=project.project_name,
            normalized_bundle_path=normalized_bundle_path,
            output_path=output_path,
        )
        return self.register_pptx_artifact(project_id, job_id, output_path)

    def register_pptx_artifact(
        self,
        project_id: str,
        job_id: str,
        pptx_path: Path,
    ) -> ArtifactRecord:
        """Register an existing PPTX file as the new primary ``pptx_main`` artifact."""
        now = datetime.now(timezone.utc)
        project_root = self.settings.projects_root / project_id
        if not pptx_path.exists():
            raise FileNotFoundError(f"PPTX not found: {pptx_path}")
        try:
            relative = pptx_path.relative_to(project_root)
        except ValueError as exc:
            raise ValueError(
                f"PPTX must reside inside project root: {pptx_path}"
            ) from exc

        artifacts = self._read_artifact_records(project_root)
        for artifact in artifacts:
            if (
                artifact.artifact_type == "pptx_main"
                and artifact.is_primary
                and artifact.status == "active"
            ):
                artifact.is_primary = False
                artifact.status = "superseded"

        new_artifact = ArtifactRecord(
            artifact_id=str(uuid4()),
            project_id=project_id,
            job_id=job_id,
            artifact_type="pptx_main",
            file_name=pptx_path.name,
            storage_path=str(relative),
            content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            is_primary=True,
            status="active",
            created_at=now.isoformat(),
        )
        artifacts.append(new_artifact)
        self._write_artifact_records(project_root, artifacts)
        return new_artifact

    def update_job_progress(
        self,
        project_id: str,
        job_id: str,
        *,
        current_stage: str,
        progress_percent: int,
        status_text: str,
    ) -> None:
        """Lightweight progress update — alias for ``mark_job_running``."""
        self.mark_job_running(
            project_id,
            job_id,
            current_stage=current_stage,
            progress_percent=progress_percent,
            status_text=status_text,
        )

    def _next_actions(self, status: ProjectStatus) -> list[NextAction]:
        if status is ProjectStatus.CREATED:
            return [NextAction.UPLOAD_SOURCE, NextAction.START_GENERATION]
        if status is ProjectStatus.UPLOADING:
            return [NextAction.UPLOAD_SOURCE, NextAction.FINALIZE_UPLOADS, NextAction.START_GENERATION]
        if status is ProjectStatus.AWAITING_CONFIRMATION:
            return [NextAction.AWAIT_CONFIRMATION]
        if status is ProjectStatus.READY_TO_GENERATE:
            return [NextAction.START_GENERATION]
        if status is ProjectStatus.COMPLETED:
            return [NextAction.DOWNLOAD_PPTX]
        return []

    def _auto_approve_confirmation(
        self,
        project_root: Path,
        project_record: ProjectRecord,
        now: datetime,
    ) -> ProjectRecord:
        confirmation = self._read_confirmation_record(project_root)
        approved_spec = confirmation.approved_spec or {
            **confirmation.suggested_spec,
            "approved": True,
            "approved_by": confirmation.approved_by or "system",
            "approval_mode": "auto",
        }
        updated_confirmation = ConfirmationRecord(
            project_id=confirmation.project_id,
            status=ConfirmationStatus.APPROVED.value,
            suggested_spec=confirmation.suggested_spec,
            approved_spec=approved_spec,
            approved_by=confirmation.approved_by or "system",
            approved_at=confirmation.approved_at or now.isoformat(),
            revision_note=confirmation.revision_note,
            created_at=confirmation.created_at,
            updated_at=now.isoformat(),
        )
        self._write_confirmation_record(project_root, updated_confirmation)

        updated_project = ProjectRecord(
            **{
                **asdict(project_record),
                "status": ProjectStatus.READY_TO_GENERATE.value,
                "status_text": "Sources normalized. Ready to start generation.",
                "updated_at": now.isoformat(),
            }
        )
        self._write_project_record(project_root, updated_project)
        return updated_project

    # ------------------------------------------------------------------
    # Extended endpoints (additions for the WeChat mini-program)
    # ------------------------------------------------------------------

    _EDITABLE_STATUSES = {
        ProjectStatus.CREATED.value,
        ProjectStatus.UPLOADING.value,
    }

    def update_project(
        self,
        project_id: str,
        request: UpdateProjectRequest,
    ) -> ProjectSummary:
        now = datetime.now(timezone.utc)
        project_root = self.settings.projects_root / project_id
        record = self._read_project_record(project_root)
        if record.status not in self._EDITABLE_STATUSES:
            raise ValueError(
                f"Project not editable in status {record.status}"
            )

        new_min = request.requested_page_min or record.requested_page_min
        new_max = request.requested_page_max or record.requested_page_max
        if new_max < new_min:
            raise ValueError("requested_page_max must be >= requested_page_min")

        updated = ProjectRecord(
            **{
                **asdict(record),
                "project_name": request.project_name or record.project_name,
                "requested_page_min": new_min,
                "requested_page_max": new_max,
                "source_type_hint": request.source_type_hint or record.source_type_hint,
                "updated_at": now.isoformat(),
            }
        )
        self._write_project_record(project_root, updated)
        return self._to_summary(updated)

    def delete_project(self, project_id: str) -> DeleteResponse:
        """Hard-delete project: DB cascade + remove on-disk files."""
        project_root = self.settings.projects_root / project_id
        # Make sure the project exists (raises FileNotFoundError if not)
        self._read_project_record(project_root)
        with session_scope() as session:
            row = session.get(orm.Project, project_id)
            if row is not None:
                session.delete(row)
        if project_root.exists():
            shutil.rmtree(project_root, ignore_errors=True)
        return DeleteResponse(project_id=project_id, deleted=True)

    def list_sources(self, project_id: str) -> list[SourceFileSummary]:
        project_root = self.settings.projects_root / project_id
        records = self._read_source_records(project_root)
        return [
            SourceFileSummary(
                source_file_id=r.source_file_id,
                project_id=r.project_id,
                original_name=r.original_name,
                stored_name=r.stored_name,
                source_kind=r.source_kind,
                role=r.role,
                size_bytes=r.size_bytes,
                status=r.status,
                storage_path=r.storage_path,
                canonical_source_path=r.canonical_source_path,
                normalized_markdown_path=r.normalized_markdown_path,
                error_message=r.error_message,
                created_at=datetime.fromisoformat(r.created_at),
            )
            for r in records
        ]

    def delete_source(self, project_id: str, source_file_id: str) -> DeleteResponse:
        project_root = self.settings.projects_root / project_id
        record = self._read_project_record(project_root)
        if record.status not in self._EDITABLE_STATUSES:
            raise ValueError(
                "Sources can only be deleted before finalize_sources"
            )

        with session_scope() as session:
            row = session.get(orm.SourceFile, source_file_id)
            if row is None or row.project_id != project_id:
                raise FileNotFoundError("Source file not found")
            for rel_path in (row.storage_path, row.canonical_source_path,
                             row.normalized_markdown_path):
                if not rel_path:
                    continue
                p = project_root / rel_path
                if p.exists():
                    p.unlink()
            session.delete(row)
        return DeleteResponse(project_id=project_id, deleted=True)

    def register_url_source(
        self,
        project_id: str,
        url: str,
        role: str = "primary_source",
    ) -> SourceUploadResponse:
        """Fetch a URL via the web_to_md helper and register it as a source."""
        import importlib.util as _ilu
        web_to_md_path = (
            Path(__file__).resolve().parents[2]
            / "skills" / "ppt-master" / "scripts" / "source_to_md" / "web_to_md.py"
        )
        spec = _ilu.spec_from_file_location("ppt_master_web_to_md", web_to_md_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("web_to_md helper unavailable")
        module = _ilu.module_from_spec(spec)
        spec.loader.exec_module(module)

        try:
            html = module.fetch_url(url)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"Failed to fetch URL: {exc}") from exc

        # Convert HTML to markdown and prepend a tiny header.
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(html, "html.parser")
        md_content = module.simple_html_to_markdown_traversal(
            module.find_main_content(soup) or soup
        )
        meta = module.extract_metadata(soup, url)
        title = meta.get("title") or url
        body = (
            f"<!--\n  Source: {url}\n-->\n\n"
            f"# {title}\n\n{md_content.strip()}\n"
        ).encode("utf-8")

        original_name = (module.sanitize_filename(title) or "fetched_url") + ".md"
        return self.register_source_upload(
            project_id=project_id,
            original_name=original_name,
            content=body,
            source_kind="markdown",
            role=role,
        )

    def list_jobs(self, project_id: str) -> list[GenerationJobSummary]:
        with session_scope() as session:
            rows = (
                session.execute(
                    select(orm.GenerationJob)
                    .where(orm.GenerationJob.project_id == project_id)
                    .order_by(orm.GenerationJob.created_at.desc())
                )
                .scalars()
                .all()
            )
            return [self._to_job_summary(self._job_to_record(r)) for r in rows]

    def list_job_events(
        self, project_id: str, job_id: str
    ) -> list[JobEventSummary]:
        with session_scope() as session:
            job = session.get(orm.GenerationJob, job_id)
            if job is None or job.project_id != project_id:
                raise FileNotFoundError("Generation job not found")
            rows = (
                session.execute(
                    select(orm.JobEvent)
                    .where(orm.JobEvent.job_id == job_id)
                    .order_by(orm.JobEvent.created_at)
                )
                .scalars()
                .all()
            )
            return [
                JobEventSummary(
                    event_id=e.id,
                    job_id=e.job_id,
                    stage=e.stage,
                    progress_percent=e.progress_percent,
                    message=e.message,
                    created_at=e.created_at,
                )
                for e in rows
            ]

    def record_job_event(
        self,
        project_id: str,
        job_id: str,
        *,
        stage: str,
        progress_percent: int,
        message: str,
    ) -> None:
        with session_scope() as session:
            job = session.get(orm.GenerationJob, job_id)
            if job is None or job.project_id != project_id:
                return
            session.add(
                orm.JobEvent(
                    job_id=job_id,
                    stage=stage,
                    progress_percent=progress_percent,
                    message=message,
                )
            )

    def is_job_cancelled(self, project_id: str, job_id: str) -> bool:
        with session_scope() as session:
            job = session.get(orm.GenerationJob, job_id)
            return bool(
                job and job.project_id == project_id
                and job.status == JobStatus.CANCELLED.value
            )

    def cancel_job(self, project_id: str, job_id: str) -> CancelJobResponse:
        now = datetime.now(timezone.utc)
        with session_scope() as session:
            job = session.get(orm.GenerationJob, job_id)
            if job is None or job.project_id != project_id:
                raise FileNotFoundError("Generation job not found")
            if job.status not in (JobStatus.QUEUED.value, JobStatus.RUNNING.value,
                                  JobStatus.RETRYING.value):
                raise ValueError(f"Cannot cancel job in status {job.status}")
            job.status = JobStatus.CANCELLED.value
            job.current_stage = "cancelled"
            job.status_text = "Cancelled by user"

            project = session.get(orm.Project, project_id)
            if project is not None:
                project.status = ProjectStatus.CANCELLED.value
                project.status_text = "Generation cancelled by user"
                project.updated_at = now
            return CancelJobResponse(
                job_id=job_id,
                project_id=project_id,
                status=JobStatus.CANCELLED,
                status_text="Cancelled by user",
            )

    def reject_confirmation(
        self,
        project_id: str,
        request: RejectConfirmationRequest,
    ) -> ConfirmationSummary:
        now = datetime.now(timezone.utc)
        project_root = self.settings.projects_root / project_id
        confirmation = self._read_confirmation_record(project_root)
        updated = ConfirmationRecord(
            project_id=confirmation.project_id,
            status=ConfirmationStatus.REVISED.value,
            suggested_spec=confirmation.suggested_spec,
            approved_spec=None,
            approved_by=None,
            approved_at=None,
            revision_note=(request.revision_note or request.reason)[:2000],
            created_at=confirmation.created_at,
            updated_at=now.isoformat(),
        )
        self._write_confirmation_record(project_root, updated)

        project = self._read_project_record(project_root)
        # Stay in AWAITING_CONFIRMATION so user can re-submit / admin can re-approve.
        if ProjectStatus(project.status) is not ProjectStatus.AWAITING_CONFIRMATION:
            updated_project = ProjectRecord(
                **{
                    **asdict(project),
                    "status": ProjectStatus.AWAITING_CONFIRMATION.value,
                    "status_text": "Confirmation rejected: " + request.reason[:120],
                    "updated_at": now.isoformat(),
                }
            )
            self._write_project_record(project_root, updated_project)
        return self._to_confirmation_summary(updated)

    def get_artifact_path(self, project_id: str, artifact_id: str) -> tuple[Path, ArtifactRecord]:
        project_root = self.settings.projects_root / project_id
        with session_scope() as session:
            row = session.get(orm.Artifact, artifact_id)
            if row is None or row.project_id != project_id:
                raise FileNotFoundError("Artifact not found")
            record = self._artifact_to_record(row)
        path = _safe_join(project_root, record.storage_path)
        if not path.exists():
            raise FileNotFoundError(f"Artifact file missing: {record.storage_path}")
        return path, record

    def get_first_svg_path(self, project_id: str) -> Path:
        """Return the first generated SVG (preview) if any."""
        project_root = self.settings.projects_root / project_id
        for sub in ("svg_final", "svg_output"):
            d = project_root / sub
            if d.exists():
                svgs = sorted(d.glob("*.svg"))
                if svgs:
                    return svgs[0]
        raise FileNotFoundError("No preview SVG available yet")

    def quota_for_user(self, user_id: str) -> QuotaSummary:
        with session_scope() as session:
            count = (
                session.execute(
                    select(orm.Project).where(orm.Project.user_id == user_id)
                )
                .scalars()
                .all()
            )
            project_count = len(count)
        # Estimate disk usage by walking project dirs (cheap for small N).
        storage_bytes = 0
        for p in count:
            d = self.settings.projects_root / p.id
            if not d.exists():
                continue
            for f in d.rglob("*"):
                if f.is_file():
                    try:
                        storage_bytes += f.stat().st_size
                    except OSError:
                        pass
        return QuotaSummary(
            user_id=user_id,
            project_count=project_count,
            project_quota=None,
            storage_bytes=storage_bytes,
            storage_quota_bytes=None,
        )

    def admin_list_projects(self, limit: int = 200) -> list[ProjectSummary]:
        with session_scope() as session:
            rows = (
                session.execute(
                    select(orm.Project)
                    .order_by(orm.Project.created_at.desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            return [self._to_summary(self._project_to_record(r)) for r in rows]
