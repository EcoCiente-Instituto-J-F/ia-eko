from __future__ import annotations

from pydantic import BaseModel, Field


class RankingEntry(BaseModel):
    id: str
    score: float
    position: int = Field(ge=1)


class RankingResponse(BaseModel):
    kind: str
    condominio_id: int
    ciclo_id: int
    entries: list[RankingEntry] = Field(default_factory=list)
    source: str = "redis_zset"


class MyRankingResponse(BaseModel):
    condominio_id: int
    ciclo_id: int
    usuario_id: int
    score: float | None = None
    position: int | None = None
    source: str = "redis_zset"
