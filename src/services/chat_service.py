from __future__ import annotations

import time

from src.agents.graph import EcoGraphRuntime
from src.api.schemas.chat import ChatRequest, ChatResponse, JudgeDecisionResponse
from src.observability.metrics import AGENTS_CALLED
from src.services.session_service import SessionService
from src.shared.context import UserContext


class ChatService:
    def __init__(self, sessions: SessionService, graph: EcoGraphRuntime):
        self.sessions = sessions
        self.graph = graph

    async def chat(
        self,
        request: ChatRequest,
        *,
        request_id: str,
        user_context: UserContext,
    ) -> ChatResponse:
        started = time.perf_counter()
        user_id = user_context.user_id
        session = await self.sessions.resolve_session(user_id, request.session_id)
        session_id = session["session_id"]

        # Uma sessão é processada em ordem no mesmo runtime. Isso evita duas
        # respostas simultâneas reordenarem append/compactação da memória.
        async with self.sessions.conversation_guard(session_id):
            await self.sessions.append_message(session_id, user_id, "user", request.mensagem)

            state = await self.graph.invoke(
                {
                    "user_context": user_context,
                    "usuario_id": user_id,
                    "session_id": session_id,
                    "request_id": request_id,
                    "mensagem": request.mensagem,
                    "perfil": user_context.perfil,
                    "condominio_id": user_context.condominio_id,
                    "agents_called": [],
                    "latencies_ms": {},
                    "sources": [],
                    "corrections": 0,
                }
            )
            answer = state.get("answer", "Não foi possível gerar uma resposta validada.")
            await self.sessions.append_message(session_id, user_id, "assistant", answer)
            await self.sessions.update_last_route(
                session_id,
                user_id,
                state.get("route", state.get("agent", "guardrail")),
            )

        agents_called = state.get("agents_called", [])
        AGENTS_CALLED.observe(len(agents_called))
        latency_ms = (time.perf_counter() - started) * 1000
        judge = state.get("judge")
        return ChatResponse(
            session_id=session_id,
            request_id=request_id,
            agent=state.get("agent", "guardrail"),
            answer=answer,
            sources=state.get("sources", []),
            agents_called=agents_called,
            latency_ms=round(latency_ms, 2),
            judge=JudgeDecisionResponse(**judge) if judge else None,
        )
