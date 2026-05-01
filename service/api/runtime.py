from __future__ import annotations

from functools import lru_cache

from service.config import get_settings
from service.core.workspace import WorkspaceManager
from service.storage.local import LocalStorageBackend
from service.workers.job_runner import InProcessJobRunner


@lru_cache(maxsize=1)
def workspace_manager() -> WorkspaceManager:
    settings = get_settings()
    storage = LocalStorageBackend()
    return WorkspaceManager(settings=settings, storage=storage)


@lru_cache(maxsize=1)
def job_runner() -> InProcessJobRunner:
    return InProcessJobRunner(workspace_manager())
