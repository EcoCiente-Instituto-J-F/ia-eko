from __future__ import annotations

import asyncio
from typing import Any

from pymongo import AsyncMongoClient

from src.core.config import Settings


class MongoDatabase:
    def __init__(self) -> None:
        self.client: AsyncMongoClient | None = None
        self.database: Any = None

    async def start(self, settings: Settings) -> None:
        if self.client is not None:
            return
        self.client = AsyncMongoClient(
            settings.mongodb_uri,
            serverSelectionTimeoutMS=settings.mongodb_timeout_ms,
            connectTimeoutMS=settings.mongodb_timeout_ms,
        )
        self.database = self.client[settings.mongodb_database]
        await asyncio.wait_for(self.client.admin.command("ping"), timeout=max(1.0, settings.mongodb_timeout_ms / 1000))

    async def ensure_indexes(self) -> None:
        if self.database is None:
            return
        await self.database.sessoes.create_index("session_id", unique=True)
        await self.database.sessoes.create_index("expira_em", expireAfterSeconds=0)
        await self.database.memoria_longo_prazo.create_index("usuario_id", unique=True)

    async def ping(self) -> bool:
        if self.client is None:
            return False
        await self.client.admin.command("ping")
        return True

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()
        self.client = None
        self.database = None


mongo_db = MongoDatabase()
