from __future__ import annotations

import pytest

AsyncMongoClient = pytest.importorskip("pymongo").AsyncMongoClient

from src.database.mongodb import MongoDatabase
from src.services.session_service import SessionService

pytestmark = pytest.mark.integration


class _RedisStub:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key: str, value: str, **_: object) -> None:
        self.values[key] = value

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)


class _RedisHolder:
    def __init__(self) -> None:
        self.client = _RedisStub()


class _Summarizer:
    async def summarize(self, previous_summary: str | None, messages: list[dict]) -> str:
        prefix = f"{previous_summary} | " if previous_summary else ""
        return prefix + " | ".join(f"{m['role']}:{m['content']}" for m in messages)


@pytest.mark.asyncio
async def test_memory_summary_and_recent_messages_persist_in_real_mongodb(integration_settings) -> None:
    if not integration_settings.test_mongodb_uri:
        pytest.skip("TEST_MONGODB_URI não configurado")

    client = AsyncMongoClient(integration_settings.test_mongodb_uri, serverSelectionTimeoutMS=3000)
    db = client[integration_settings.test_mongodb_database]
    mongo = MongoDatabase()
    mongo.client = client
    mongo.database = db
    redis = _RedisHolder()
    settings = integration_settings.with_overrides(
        storage_mode="external",
        memory_max_messages=20,
        memory_keep_recent_messages=6,
    )
    service = SessionService(settings, mongo, redis)  # type: ignore[arg-type]
    session_id: str | None = None
    try:
        await client.admin.command("ping")
        session = await service.create_session(900001)
        session_id = session["session_id"]
        for index in range(1, 22):
            role = "user" if index % 2 else "assistant"
            await service.append_message(session_id, 900001, role, f"mongo-{index}")

        assert await service.compact_memory_if_needed(session_id, 900001, _Summarizer()) is True

        persisted = await db.sessoes.find_one({"session_id": session_id}, {"_id": 0})
        assert persisted is not None
        assert persisted["memory_summary"]
        assert len(persisted["messages"]) == 6
        assert persisted["memory_revision"] == 1

        recovered = await service.get_memory_context(session_id, 900001)
        assert recovered["memory_summary"] == persisted["memory_summary"]
        assert recovered["recent_messages"] == persisted["messages"]
    finally:
        if session_id:
            await db.sessoes.delete_one({"session_id": session_id})
        await client.close()
