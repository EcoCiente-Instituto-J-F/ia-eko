from __future__ import annotations

import httpx
import pytest

from src.integrations.calendar.client import CalendarApiClient


@pytest.mark.asyncio
async def test_calendar_health_mobile_api_documentation() -> None:
    paths: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200 if request.url.path == "/api-docs" else 404)

    client = CalendarApiClient("https://mobile.example", transport=httpx.MockTransport(responder))
    try:
        assert await client.health() == "ok"
        assert paths == ["/api-docs"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_calendar_health_legacy_documentation() -> None:
    paths: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(404 if request.url.path == "/api-docs" else 200)

    client = CalendarApiClient("https://legacy.example", transport=httpx.MockTransport(responder))
    try:
        assert await client.health() == "ok"
        assert paths == ["/api-docs", "/v3/api-docs"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_calendar_health_unavailable_without_openapi() -> None:
    client = CalendarApiClient(
        "https://mobile.example",
        transport=httpx.MockTransport(lambda _: httpx.Response(404)),
    )
    try:
        assert await client.health() == "unavailable"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_calendar_health_does_not_mask_forbidden_as_legacy() -> None:
    paths: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(403)

    client = CalendarApiClient("https://mobile.example", transport=httpx.MockTransport(responder))
    try:
        assert await client.health() == "unavailable"
        assert paths == ["/api-docs"]
    finally:
        await client.close()
