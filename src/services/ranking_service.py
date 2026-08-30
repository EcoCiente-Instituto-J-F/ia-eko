from __future__ import annotations

import time
from typing import Literal

from redis.exceptions import RedisError

from src.api.schemas.ranking import MyRankingResponse, RankingEntry, RankingResponse
from src.database.redis import RedisDatabase
from src.observability.metrics import DB_LATENCY


class RankingUnavailable(RuntimeError):
    pass


class RankingService:
    """Leitura de rankings Redis. O Redis é projeção; PostgreSQL continua fonte oficial dos fatos."""

    def __init__(self, redis_db: RedisDatabase):
        self.redis_db = redis_db

    @staticmethod
    def key(kind: Literal["moradores", "torres"], condominio_id: int, ciclo_id: int) -> str:
        return f"ranking:{kind}:condominio:{condominio_id}:ciclo:{ciclo_id}"

    async def top_rows(self, kind: Literal["moradores", "torres"], condominio_id: int, ciclo_id: int, limit: int = 10) -> list[dict[str, float | int | str]]:
        client = self.redis_db.client
        if client is None:
            raise RankingUnavailable("Redis não está disponível para consulta de ranking.")
        try:
            started = time.perf_counter()
            rows = await client.zrevrange(self.key(kind, condominio_id, ciclo_id), 0, limit - 1, withscores=True)
            DB_LATENCY.labels(backend="redis", operation=f"ranking_{kind}").observe(time.perf_counter() - started)
            return [
                {"id": str(member), "score": float(score), "position": index + 1}
                for index, (member, score) in enumerate(rows)
            ]
        except RedisError as exc:
            raise RankingUnavailable("Falha ao consultar ranking no Redis.") from exc

    async def moradores(self, condominio_id: int, ciclo_id: int, limit: int = 10) -> RankingResponse:
        rows = await self.top_rows("moradores", condominio_id, ciclo_id, limit)
        return RankingResponse(kind="moradores", condominio_id=condominio_id, ciclo_id=ciclo_id, entries=[RankingEntry(**row) for row in rows])

    async def torres(self, condominio_id: int, ciclo_id: int, limit: int = 10) -> RankingResponse:
        rows = await self.top_rows("torres", condominio_id, ciclo_id, limit)
        return RankingResponse(kind="torres", condominio_id=condominio_id, ciclo_id=ciclo_id, entries=[RankingEntry(**row) for row in rows])

    async def position(self, usuario_id: int, condominio_id: int, ciclo_id: int) -> dict[str, float | int | None]:
        client = self.redis_db.client
        if client is None:
            raise RankingUnavailable("Redis não está disponível para consulta de ranking.")
        key = self.key("moradores", condominio_id, ciclo_id)
        try:
            started = time.perf_counter()
            score = await client.zscore(key, str(usuario_id))
            rank = await client.zrevrank(key, str(usuario_id))
            DB_LATENCY.labels(backend="redis", operation="ranking_me").observe(time.perf_counter() - started)
            return {"score": float(score) if score is not None else None, "position": int(rank) + 1 if rank is not None else None}
        except RedisError as exc:
            raise RankingUnavailable("Falha ao consultar posição no Redis.") from exc

    async def me(self, usuario_id: int, condominio_id: int, ciclo_id: int) -> MyRankingResponse:
        values = await self.position(usuario_id, condominio_id, ciclo_id)
        return MyRankingResponse(condominio_id=condominio_id, ciclo_id=ciclo_id, usuario_id=usuario_id, **values)
