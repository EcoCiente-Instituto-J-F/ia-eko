from __future__ import annotations

import uuid

import pytest
AsyncMongoClient = pytest.importorskip("pymongo").AsyncMongoClient

from src.core.config import Settings

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_real_mongodb_isolated_collection(integration_settings: Settings) -> None:
    if not integration_settings.test_mongodb_uri:
        pytest.skip("TEST_MONGODB_URI não configurado")
    client = AsyncMongoClient(integration_settings.test_mongodb_uri, serverSelectionTimeoutMS=3000)
    collection_name = f"integration_{uuid.uuid4().hex}"
    db = client[integration_settings.test_mongodb_database]
    try:
        await client.admin.command("ping")
        await db[collection_name].insert_one({"kind": "integration", "ok": True})
        found = await db[collection_name].find_one({"kind": "integration"})
        assert found and found["ok"] is True
    finally:
        await db.drop_collection(collection_name)
        await client.close()
