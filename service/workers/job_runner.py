"""In-process generation worker.

Strategy:
- If ``OPENAI_API_KEY`` is set, run the real LLM pipeline.
- Otherwise (or on real-pipeline failure), fall back to the deterministic
  text-based PPTX export so the API contract is always honoured.
"""
from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock

from service.core.workspace import WorkspaceManager
from service.workers.pipeline import PipelineError, run_real_pipeline


logger = logging.getLogger(__name__)


class JobCancelled(Exception):
    """Raised internally when the worker detects an external cancel request."""


class InProcessJobRunner:
    """Single-worker in-process runner for the service."""

    def __init__(self, workspace_manager: WorkspaceManager) -> None:
        self.workspace_manager = workspace_manager
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ppt-service")
        self._futures: dict[str, Future[None]] = {}
        self._lock = Lock()

    def enqueue_generation(self, project_id: str, job_id: str) -> None:
        with self._lock:
            existing = self._futures.get(job_id)
            if existing is not None and not existing.done():
                return
            future = self.executor.submit(self._run_generation_job, project_id, job_id)
            self._futures[job_id] = future

    def _run_generation_job(self, project_id: str, job_id: str) -> None:
        try:
            self._abort_if_cancelled(project_id, job_id)
            self.workspace_manager.mark_job_running(
                project_id,
                job_id,
                current_stage="preparing_context",
                progress_percent=15,
                status_text="Worker started generation preparation.",
            )
            self.workspace_manager.record_job_event(
                project_id, job_id,
                stage="preparing_context", progress_percent=15,
                message="Worker started generation preparation.",
            )
            self.workspace_manager.materialize_generation_context(project_id, job_id)
            self._abort_if_cancelled(project_id, job_id)

            settings = self.workspace_manager.settings
            used_real = False

            if settings.openai_api_key:
                try:
                    def _progress(stage: str, percent: int, status_text: str) -> None:
                        self._abort_if_cancelled(project_id, job_id)
                        self.workspace_manager.update_job_progress(
                            project_id,
                            job_id,
                            current_stage=stage,
                            progress_percent=percent,
                            status_text=status_text,
                        )
                        self.workspace_manager.record_job_event(
                            project_id, job_id,
                            stage=stage, progress_percent=percent,
                            message=status_text,
                        )

                    pptx_path = run_real_pipeline(
                        self.workspace_manager,
                        project_id,
                        job_id,
                        progress=_progress,
                    )
                    self.workspace_manager.register_pptx_artifact(project_id, job_id, pptx_path)
                    used_real = True
                except JobCancelled:
                    raise
                except PipelineError as exc:
                    logger.warning(
                        "Real pipeline failed for project=%s job=%s: %s — using fallback",
                        project_id, job_id, exc,
                    )
                    self.workspace_manager.record_job_event(
                        project_id, job_id,
                        stage="real_pipeline_failed", progress_percent=80,
                        message=f"Real pipeline failed, using fallback: {exc}",
                    )

            if not used_real:
                self._abort_if_cancelled(project_id, job_id)
                self.workspace_manager.update_job_progress(
                    project_id,
                    job_id,
                    current_stage="writing_artifacts",
                    progress_percent=80,
                    status_text="Worker materialized design artifacts (fallback).",
                )
                self.workspace_manager.export_fallback_pptx_artifact(project_id, job_id)

            self._abort_if_cancelled(project_id, job_id)
            self.workspace_manager.complete_generation_job(
                project_id,
                job_id,
                current_stage="exported",
                progress_percent=100,
                status_text=(
                    "Worker completed real PPTX export."
                    if used_real
                    else "Worker completed fallback PPTX export."
                ),
            )
            self.workspace_manager.record_job_event(
                project_id, job_id,
                stage="exported", progress_percent=100,
                message="Generation completed.",
            )
        except JobCancelled:
            logger.info("Generation cancelled: project=%s job=%s", project_id, job_id)
            self.workspace_manager.record_job_event(
                project_id, job_id,
                stage="cancelled", progress_percent=0,
                message="Worker observed cancel request and stopped.",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Generation worker crashed: %s", exc)
            self.workspace_manager.fail_generation_job(
                project_id,
                job_id,
                f"Generation worker failed: {exc}",
            )

    def _abort_if_cancelled(self, project_id: str, job_id: str) -> None:
        if self.workspace_manager.is_job_cancelled(project_id, job_id):
            raise JobCancelled()
