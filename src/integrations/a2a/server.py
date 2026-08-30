from __future__ import annotations

from a2a.helpers import get_message_text, new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import Role
from fastapi import FastAPI

from src.core.config import Settings
from src.integrations.a2a.agent_card import build_agent_card
from src.services.rag_service import RAGService


class EcoEducationalExecutor(AgentExecutor):
    """A2A real: recebe mensagem externa e produz uma única Message A2A fundamentada no RAG."""

    def __init__(self, rag: RAGService):
        self.rag = rag

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        question = get_message_text(context.message)
        rag_context, sources = await self.rag.search(question, k=3)
        if rag_context:
            excerpt = " ".join(rag_context.split())[:1200]
            source_names = ", ".join(sorted({source.title for source in sources}))
            answer = f"Evidência EcoCiente: {excerpt}\n\nFontes: {source_names}"
        else:
            answer = "A base EcoCiente não contém evidência suficiente para esta pergunta."
        await event_queue.enqueue_event(new_text_message(answer, role=Role.ROLE_AGENT))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await event_queue.enqueue_event(
            new_text_message("Solicitação A2A cancelada.", role=Role.ROLE_AGENT)
        )


def add_a2a_endpoints(app: FastAPI, settings: Settings, rag: RAGService) -> None:
    card = build_agent_card(settings)
    handler = DefaultRequestHandler(
        agent_executor=EcoEducationalExecutor(rag),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/a2a/jsonrpc/"),
    )
