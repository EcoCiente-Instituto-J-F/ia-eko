from __future__ import annotations

from pydantic import BaseModel, Field


class SourceResponse(BaseModel):
    title: str
    source: str
    chunk: int | None = None
    url: str | None = None


class ServiceStatusResponse(BaseModel):
    status: str
    detail: str | None = None


class ErrorResponse(BaseModel):
    detail: str = Field(description="Descrição segura do erro.")
