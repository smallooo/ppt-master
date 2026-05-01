from __future__ import annotations

from enum import Enum


class ProjectStatus(str, Enum):
    CREATED = "created"
    UPLOADING = "uploading"
    NORMALIZING = "normalizing"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    READY_TO_GENERATE = "ready_to_generate"
    GENERATING = "generating"
    POST_PROCESSING = "post_processing"
    EXPORTING = "exporting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NextAction(str, Enum):
    UPLOAD_SOURCE = "upload_source"
    FINALIZE_UPLOADS = "finalize_uploads"
    AWAIT_CONFIRMATION = "await_confirmation"
    START_GENERATION = "start_generation"
    DOWNLOAD_PPTX = "download_pptx"


class ConfirmationStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REVISED = "revised"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"
