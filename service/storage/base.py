from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class StorageBackend(ABC):
    """Minimal storage contract for the service MVP."""

    @abstractmethod
    def ensure_dir(self, path: Path) -> Path:
        raise NotImplementedError

    @abstractmethod
    def write_text(self, path: Path, content: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def read_text(self, path: Path) -> str:
        raise NotImplementedError

    @abstractmethod
    def exists(self, path: Path) -> bool:
        raise NotImplementedError
