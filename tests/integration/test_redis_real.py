from __future__ import annotations

import uuid

import pytest
Redis = pytest.importorskip("redis.asyncio").Redis

from src.core.config import Settings

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_real_redis_isolated_namespace(integration_settings: Settings) -> None:
    if not integration_settings.test_redis_url:
        pytest.skip("TEST_REDIS_URL não configurado")
    client = Redis.from_url(integration_settings.test_redis_url, decode_responses=True)
    key = f"{integration_settings.test_redis_prefix}{uuid.uuid4().hex}"
    try:
        assert await client.ping()
        await client.set(key, "ok", ex=60)
        assert await client.get(key) == "ok"
    finally:
        await client.delete(key)
        await client.aclose()
