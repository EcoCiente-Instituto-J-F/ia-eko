from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from src.api.schemas.common import SourceResponse


class ChatRequest(BaseModel):
    # Se informado, usuario_id deve coincidir com a identidade autenticada.
    usuario_id: int | None = Field(default=None, gt=0)
    token: str | None = Field(default=None, min_length=1, repr=False)
    session_id: str | None = Field(default=None, min_length=8, max_length=80)
    mensagem: str = Field(min_length=1, max_length=8000)

    @field_validator("mensagem")
    @classmethod
    def mensagem_nao_vazia(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("mensagem não pode conter apenas espaços")
        return value

    @field_validator("token")
    @classmethod
    def token_nao_vazio(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class JudgeDecisionResponse(BaseModel):
    aprovado: bool
    motivo: str
    necessita_correcao: bool
    # Categoria fechada (ver src/agents/output_parsing.py). O texto censurado
    # pelo juiz nunca é exposto aqui: ele já é o próprio `answer`.
    categoria: str | None = None


class TokenUsageResponse(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class QuotaInfo(BaseModel):
    limite_diario: int | None
    usadas_hoje: int
    restantes_hoje: int | None
    renova_em: datetime

    @classmethod
    def from_status(cls, status: Any) -> "QuotaInfo":
        return cls(
            limite_diario=status.limit,
            usadas_hoje=status.used,
            restantes_hoje=status.remaining,
            renova_em=status.reset_at,
        )


class ChatResponse(BaseModel):
    session_id: str
    request_id: str
    agent: str
    answer: str
    sources: list[SourceResponse] = Field(default_factory=list)
    agents_called: list[str] = Field(default_factory=list)
    latency_ms: float
    judge: JudgeDecisionResponse | None = None
    usage: TokenUsageResponse | None = None
    quota: QuotaInfo | None = None
