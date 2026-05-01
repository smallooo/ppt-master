from __future__ import annotations

from typing import Generic, TypeVar
from uuid import uuid4

from pydantic import BaseModel, Field


PayloadT = TypeVar("PayloadT")


class ResponseEnvelope(BaseModel, Generic[PayloadT]):
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    success: bool = True
    message: str = "ok"
    data: PayloadT
