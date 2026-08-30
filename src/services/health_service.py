from __future__ import annotations

import asyncio

import httpx
import psycopg2
from psycopg2 import pool

from src.core.config import Settings
from src.database.postgres import PostgresDatabase, PostgresUnavailable
from src.integrations.calendar.client import CalendarApiClient
from src.services.rag_service import RAGService
from src.services.session_service import SessionService


class HealthService:
    def __init__(
        self,
        settings: Settings,
        sessions: SessionService,
        rag: RAGService,
        postgres: PostgresDatabase,
        *,
        calendar_api: CalendarApiClient | None = None,
    ):
        self.settings = settings
        self.sessions = sessions
        self.rag = rag
        self.postgres = postgres
        self.calendar_api = calendar_api

    async def check(self) -> dict[str, str]:
        services = await self.sessions.health()
        services["rag"] = self.rag.health()
        services["llm"] = await self._llm_health()
        services["postgres"] = await self._postgres_health()
        services["calendar_api"] = await self._calendar_health()
        return services

    async def _calendar_health(self) -> str:
        if self.calendar_api is None:
            return "not_configured"
        return await self.calendar_api.health()

    async def _llm_health(self) -> str:
        if self.settings.llm_provider == "mock":
            return "mock"
        if self.settings.llm_provider != "ollama":
            return "configured"
        try:
            async with httpx.AsyncClient(timeout=1.5) as client:
                response = await client.get(f"{self.settings.ollama_base_url.rstrip('/')}/api/tags")
            return "ok" if response.is_success else "error"
        except httpx.HTTPError:
            return "error"

    async def _postgres_health(self) -> str:
        if not self.settings.postgres_url:
            return "not_configured"
        try:
            return "ok" if await asyncio.wait_for(asyncio.to_thread(self.postgres.ping), timeout=3) else "error"
        except (PostgresUnavailable, psycopg2.Error, pool.PoolError, TimeoutError, OSError):
            return "error"
