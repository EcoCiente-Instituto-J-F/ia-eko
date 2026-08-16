from __future__ import annotations

import asyncio

import httpx

from src.core.config import Settings
from src.services.rag_service import RAGService
from src.services.session_service import SessionService


class HealthService:
    def __init__(self, settings: Settings, sessions: SessionService, rag: RAGService):
        self.settings = settings
        self.sessions = sessions
        self.rag = rag


    async def _postgres_health(self) -> str:
        if not self.settings.postgres_url:
            return "not_configured"

        def ping() -> bool:
            import psycopg2

            conn = psycopg2.connect(self.settings.postgres_url, connect_timeout=2)
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
                return True
            finally:
                conn.close()

        try:
            ok = await asyncio.wait_for(asyncio.to_thread(ping), timeout=3)
            return "ok" if ok else "error"
        except Exception:
            return "error"
