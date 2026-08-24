from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from src.api.schemas.common import SourceResponse


class ChatRequest(BaseModel):
    # usuario_id é mantido temporariamente para compatibilidade; a identidade
    # efetiva sempre vem do token validado pela API externa.
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


class ChatResponse(BaseModel):
    session_id: str
    request_id: str
    agent: str
    answer: str
    sources: list[SourceResponse] = Field(default_factory=list)
    agents_called: list[str] = Field(default_factory=list)
    latency_ms: float
    judge: JudgeDecisionResponse | None = None
