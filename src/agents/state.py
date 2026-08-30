from __future__ import annotations

from typing import Any, TypedDict

from src.api.schemas.common import SourceResponse
from src.shared.context import UserContext


class EcoState(TypedDict, total=False):
    user_context: UserContext
    usuario_id: int
    session_id: str
    request_id: str
    mensagem: str
    perfil: str
    condominio_id: int | None
    memory_context: dict[str, Any]
    memory_compaction_needed: bool
    memory_compaction_error: str | None
    route: str
    agent: str
    answer: str
    candidate_answer: str
    sources: list[SourceResponse]
    agents_called: list[str]
    judge: dict[str, Any]
    corrections: int
    blocked: bool
    blocked_reason: str
    latencies_ms: dict[str, float]
