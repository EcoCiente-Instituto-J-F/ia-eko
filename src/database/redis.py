from __future__ import annotations

from redis.asyncio import Redis

from src.core.config import Settings


class RedisDatabase:
    def __init__(self) -> None:
        self.client: Redis | None = None

    async def start(self, settings: Settings) -> None:
        if self.client is not None:
            return
        self.client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.redis_socket_timeout,
            socket_timeout=settings.redis_socket_timeout,
        )
        await self.client.ping()

    async def ping(self) -> bool:
        return bool(self.client is not None and await self.client.ping())

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()
        self.client = None


redis_db = RedisDatabase()
