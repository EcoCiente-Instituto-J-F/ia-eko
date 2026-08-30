from __future__ import annotations

from typing import Any

from langchain.tools import tool
from pydantic import BaseModel, Field

from src.database.redis import redis_db
from src.services.ranking_service import RankingService, RankingUnavailable


def _service() -> RankingService:
    return RankingService(redis_db)


class RankingTopArgs(BaseModel):
    condominio_id: int = Field(gt=0)
    ciclo_id: int = Field(gt=0)
    limit: int = Field(default=10, ge=1, le=100)


@tool("consultar_ranking_moradores", args_schema=RankingTopArgs)
async def consultar_ranking_moradores(condominio_id: int, ciclo_id: int, limit: int = 10) -> dict[str, Any]:
    """Consulta somente leitura do ZSET de moradores do condomínio autorizado."""
    try:
        entries = await _service().top_rows("moradores", condominio_id, ciclo_id, limit)
        return {"status": "ok", "kind": "moradores", "condominio_id": condominio_id, "ciclo_id": ciclo_id, "entries": entries}
    except RankingUnavailable as exc:
        return {"status": "unavailable", "message": str(exc)}


@tool("consultar_ranking_torres", args_schema=RankingTopArgs)
async def consultar_ranking_torres(condominio_id: int, ciclo_id: int, limit: int = 10) -> dict[str, Any]:
    """Consulta somente leitura do ZSET de torres do condomínio autorizado."""
    try:
        entries = await _service().top_rows("torres", condominio_id, ciclo_id, limit)
        return {"status": "ok", "kind": "torres", "condominio_id": condominio_id, "ciclo_id": ciclo_id, "entries": entries}
    except RankingUnavailable as exc:
        return {"status": "unavailable", "message": str(exc)}


class MyRankingArgs(BaseModel):
    usuario_id: int = Field(gt=0)
    condominio_id: int = Field(gt=0)
    ciclo_id: int = Field(gt=0)


@tool("consultar_minha_posicao_ranking", args_schema=MyRankingArgs)
async def consultar_minha_posicao_ranking(usuario_id: int, condominio_id: int, ciclo_id: int) -> dict[str, Any]:
    """Consulta a posição do próprio usuário sem alterar o Redis."""
    try:
        values = await _service().position(usuario_id, condominio_id, ciclo_id)
        return {"status": "ok", "usuario_id": usuario_id, "condominio_id": condominio_id, "ciclo_id": ciclo_id, **values}
    except RankingUnavailable as exc:
        return {"status": "unavailable", "message": str(exc)}


TOOLS = [consultar_ranking_moradores, consultar_ranking_torres, consultar_minha_posicao_ranking]
