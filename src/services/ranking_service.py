from __future__ import annotations

import time

from redis.asyncio import Redis

from src.api.schemas.ranking import MyRankingResponse, RankingEntry, RankingResponse
from src.core.config import Settings
from src.observability.metrics import DB_LATENCY


class RankingService:
    """Redis ZSET é projeção de leitura; PostgreSQL permanece a fonte persistente dos pontos."""

    def __init__(self, settings: Settings, redis_client: Redis | None):
        self.settings = settings
        self.redis = redis_client

    @staticmethod
    def moradores_key(condominio_id: int, ciclo_id: int) -> str:
        return f"ranking:moradores:condominio:{condominio_id}:ciclo:{ciclo_id}"

    @staticmethod
    def torres_key(condominio_id: int, ciclo_id: int) -> str:
        return f"ranking:torres:condominio:{condominio_id}:ciclo:{ciclo_id}"

    async def _top(self, key: str, kind: str, condominio_id: int, ciclo_id: int, limit: int) -> RankingResponse:
        if self.redis is None:
            return RankingResponse(kind=kind, condominio_id=condominio_id, ciclo_id=ciclo_id, entries=[])
        started = time.perf_counter()
        rows = await self.redis.zrevrange(key, 0, limit - 1, withscores=True)
        DB_LATENCY.labels(backend="redis", operation=f"ranking_{kind}").observe(time.perf_counter() - started)
        entries = [
            RankingEntry(id=str(member), score=float(score), position=index + 1)
            for index, (member, score) in enumerate(rows)
        ]
        return RankingResponse(kind=kind, condominio_id=condominio_id, ciclo_id=ciclo_id, entries=entries)

    async def moradores(self, condominio_id: int, ciclo_id: int, limit: int = 10) -> RankingResponse:
        return await self._top(self.moradores_key(condominio_id, ciclo_id), "moradores", condominio_id, ciclo_id, limit)

    async def torres(self, condominio_id: int, ciclo_id: int, limit: int = 10) -> RankingResponse:
        return await self._top(self.torres_key(condominio_id, ciclo_id), "torres", condominio_id, ciclo_id, limit)

    async def me(self, usuario_id: int, condominio_id: int, ciclo_id: int) -> MyRankingResponse:
        if self.redis is None:
            return MyRankingResponse(condominio_id=condominio_id, ciclo_id=ciclo_id, usuario_id=usuario_id)
        key = self.moradores_key(condominio_id, ciclo_id)
        started = time.perf_counter()
        score = await self.redis.zscore(key, str(usuario_id))
        rank = await self.redis.zrevrank(key, str(usuario_id))
        DB_LATENCY.labels(backend="redis", operation="ranking_me").observe(time.perf_counter() - started)
        return MyRankingResponse(
            condominio_id=condominio_id,
            ciclo_id=ciclo_id,
            usuario_id=usuario_id,
            score=float(score) if score is not None else None,
            position=int(rank) + 1 if rank is not None else None,
        )
