from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SessionCreateRequest(BaseModel):
    usuario_id: int = Field(gt=0)


class SessionResponse(BaseModel):
    session_id: str
    usuario_id: int
    status: str
    created_at: datetime
    updated_at: datetime
    ultima_rota: str | None = None
    resumo_parcial: str = ""
