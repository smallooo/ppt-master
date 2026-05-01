from __future__ import annotations

from pathlib import Path

from service.storage.base import StorageBackend


class LocalStorageBackend(StorageBackend):
    """Filesystem-backed storage implementation for the service MVP."""

    def ensure_dir(self, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def exists(self, path: Path) -> bool:
        return path.exists()
