from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.agents.graph import EcoGraphRuntime
from src.api.routes import agents, chat, health, observability, ranking, root, sessions
from src.core.config import Settings
from src.core.logging import configure_logging
from src.database.mongodb import mongo_db
from src.database.postgres import postgres_db
from src.database.redis import redis_db
from src.integrations.a2a.server import add_a2a_endpoints
from src.integrations.auth.client import AuthApiClient
from src.integrations.calendar.client import CalendarApiClient
from src.integrations.mcp.client import CalendarMcpClient
from src.observability.middleware import RequestContextMiddleware
from src.security.authentication import AuthenticationService
from src.services.chat_service import ChatService
from src.services.health_service import HealthService
from src.services.rag_service import RAGService
from src.services.ranking_service import RankingService
from src.services.session_service import SessionService


def create_app(settings_override: Settings | None = None) -> FastAPI:
    app_settings = settings_override or Settings.from_env()
    configure_logging(app_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        auth_client = (
            AuthApiClient(
                app_settings.auth_api_url,
                timeout=app_settings.auth_api_timeout,
                retries=app_settings.auth_api_retries,
            )
            if app_settings.auth_api_url
            else None
        )
        authentication_service = AuthenticationService(auth_client)

        calendar_api = (
            CalendarApiClient(
                app_settings.calendar_api_url,
                timeout=app_settings.calendar_api_timeout,
                retries=app_settings.calendar_api_retries,
            )
            if app_settings.calendar_api_url
            else None
        )
        calendar_mcp = CalendarMcpClient(calendar_api, app_settings)

        sessions_service = SessionService(app_settings, mongo_db, redis_db)
        rag_service = RAGService(app_settings)

        try:
            await asyncio.to_thread(postgres_db.start, app_settings)
            await sessions_service.start()
            await rag_service.start()

            ranking_service = RankingService(redis_db)
            graph_runtime = EcoGraphRuntime(app_settings, sessions_service, rag_service, calendar_mcp)

            app.state.settings = app_settings
            app.state.authentication = authentication_service
            app.state.calendar_api = calendar_api
            app.state.calendar_mcp = calendar_mcp
            app.state.sessions = sessions_service
            app.state.rag = rag_service
            app.state.graph = graph_runtime
            app.state.chat = ChatService(sessions_service, graph_runtime)
            app.state.rankings = ranking_service
            app.state.health_service = HealthService(
                app_settings,
                sessions_service,
                rag_service,
                postgres_db,
                calendar_api=calendar_api,
            )

            yield
        finally:
            if calendar_api is not None:
                await calendar_api.close()
            await authentication_service.close()
            await rag_service.close()
            await sessions_service.close()
            await mongo_db.close()
            await redis_db.close()
            await asyncio.to_thread(postgres_db.close)

    app = FastAPI(
        title=app_settings.app_name,
        version=app_settings.app_version,
        description="API multiagente do EcoCiente com LangChain, LangGraph, RAG, memória, MCP, A2A e observabilidade.",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    app.add_middleware(RequestContextMiddleware)
    app.include_router(root.router)
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(sessions.router)
    app.include_router(agents.router)
    app.include_router(ranking.router)
    app.include_router(observability.router)

    class _RagProxy:
        async def search(self, *args, **kwargs):
            return await app.state.rag.search(*args, **kwargs)

    add_a2a_endpoints(app, app_settings, _RagProxy())  # type: ignore[arg-type]
    return app


app = create_app()
