from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.agents.graph import EcoGraphRuntime
from src.api.routes import agents, chat, health, observability, ranking, root, sessions
from src.core.config import Settings
from src.core.logging import configure_logging
from src.database.mongodb import mongo_db
from src.database.neo4j import neo4j_db
from src.database.postgres import postgres_db
from src.database.redis import redis_db
from src.integrations.a2a.server import add_a2a_endpoints
from src.integrations.auth.client import AuthApiClient
from src.integrations.calendar.client import CalendarApiClient
from src.integrations.mcp.client import CalendarMcpClient
from src.observability.middleware import RequestContextMiddleware
from src.observability.tracing import TracingService
from src.security.authentication import AuthenticationService
from src.services.chat_service import ChatService
from src.services.health_service import HealthService
from src.services.qdrant_service import QdrantFaqService
from src.services.quota_service import QuotaService
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
        # Langfuse/LangSmith opcional; TRACING_PROVIDER=none não importa nada.
        tracing = TracingService(app_settings)
        # FAQ por busca vetorial (sem LLM). Cliente e modelo de embedding são
        # criados no primeiro uso, então isto não conecta nem baixa nada aqui.
        faq_search = QdrantFaqService(app_settings) if app_settings.faq_backend == "qdrant" else None

        try:
            await asyncio.to_thread(postgres_db.start, app_settings)
            await sessions_service.start()
            await rag_service.start()

            # Neo4j é uma camada opcional de inteligência sobre relacionamentos
            # (docs/REQUISITOS_E_FLUXOS.md); sua ausência não deve derrubar a API.
            try:
                await neo4j_db.start(app_settings)
            except Exception:
                logging.getLogger("ecociente.main").warning(
                    "Neo4j indisponível na inicialização; o agente 'grafo' responderá em modo indisponível.",
                    exc_info=True,
                )

            ranking_service = RankingService(redis_db)
            graph_runtime = EcoGraphRuntime(
                app_settings,
                sessions_service,
                rag_service,
                calendar_mcp,
                tracing=tracing,
                faq_search=faq_search,
            )
            # Usa o mesmo Redis das sessões; em STORAGE_MODE=memory (ou fallback)
            # o cliente é None e os contadores ficam em memória do processo.
            quota_service = QuotaService(app_settings, redis_db)

            app.state.settings = app_settings
            app.state.authentication = authentication_service
            app.state.calendar_api = calendar_api
            app.state.calendar_mcp = calendar_mcp
            app.state.sessions = sessions_service
            app.state.rag = rag_service
            app.state.graph = graph_runtime
            app.state.chat = ChatService(sessions_service, graph_runtime, quotas=quota_service, tracing=tracing)
            app.state.quotas = quota_service
            app.state.tracing = tracing
            app.state.rankings = ranking_service
            app.state.health_service = HealthService(
                app_settings,
                sessions_service,
                rag_service,
                postgres_db,
                calendar_api=calendar_api,
                neo4j=neo4j_db,
                qdrant=faq_search,
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
            await neo4j_db.close()
            if faq_search is not None:
                await faq_search.close()
            await asyncio.to_thread(postgres_db.close)
            # Envia os spans pendentes antes de o processo encerrar.
            await asyncio.to_thread(tracing.shutdown)

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
