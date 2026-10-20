from __future__ import annotations

import asyncio
import logging
import time
from contextlib import aclosing
from typing import Any, AsyncIterator

from src.agents.graph import EcoGraphRuntime
from src.api.schemas.chat import ChatRequest, ChatResponse, JudgeDecisionResponse, QuotaInfo, TokenUsageResponse
from src.observability.cost import record_chat
from src.observability.metrics import AGENTS_CALLED, TOKENS_PER_CHAT
from src.observability.tracing import ConversationTrace, TracingService, conversation_trace
from src.security.guardrails import sanitize_output, validate_input
from src.security.request_context import authenticated_request_context
from src.services.quota_service import QuotaExceeded, QuotaService, QuotaStatus
from src.services.session_service import SessionBusy, SessionService
from src.shared.context import UserContext

logger = logging.getLogger("ecociente.chat")

# Rótulos exibidos no stream de progresso (nós internos agrupados).
_PROGRESS_LABELS = {
    "guardrail_entrada": "Verificando a mensagem",
    "memoria": "Recuperando o contexto da conversa",
    "verificar_compactacao": "Recuperando o contexto da conversa",
    "resumir_memoria": "Consolidando a memória da conversa",
    "orquestrador": "Escolhendo o especialista",
    "faq": "Consultando a base do EcoCiente",
    "educacional": "Consultando o material educativo",
    "analytics": "Consultando seus dados",
    "coletas": "Consultando o calendário de coletas",
    "grafo": "Consultando a rede de relacionamentos",
    "juiz_saida": "Validando a resposta",
    "correcao": "Revisando a resposta",
    "guardrail_saida": "Finalizando",
}


class ChatService:
    def __init__(
        self,
        sessions: SessionService,
        graph: EcoGraphRuntime,
        quotas: QuotaService | None = None,
        tracing: TracingService | None = None,
    ):
        self.sessions = sessions
        self.graph = graph
        self.quotas = quotas
        self.tracing = tracing
        self._remember_locks: dict[int, asyncio.Lock] = {}

    # ------------------------------------------------------------------ #
    # Preparação comum ao /chat e ao /chat/stream
    # ------------------------------------------------------------------ #

    async def admit(self, request: ChatRequest, user_context: UserContext) -> tuple[dict[str, Any], QuotaStatus | None]:
        """Sessão + limites, do mais barato ao mais caro. Nada aqui chama LLM.

        No /chat/stream a rota chama isto ANTES de abrir o stream, para que um
        limite estourado vire HTTP 429 comum (e não um evento de erro dentro
        de uma resposta 200 já iniciada)."""
        if self.quotas is not None:
            await self.quotas.check_rate(user_context)
        session = await self.sessions.resolve_session(user_context.user_id, request.session_id)
        quota_status = None
        if self.quotas is not None:
            self.quotas.check_session(user_context, session)
            quota_status = await self.quotas.reserve_daily(user_context)
        return session, quota_status

    @staticmethod
    def _initial_state(request: ChatRequest, request_id: str, session_id: str, user_context: UserContext) -> dict[str, Any]:
        safe_user_context = user_context.without_token()
        return {
            "user_context": safe_user_context,
            "usuario_id": user_context.user_id,
            "session_id": session_id,
            "request_id": request_id,
            "mensagem": request.mensagem,
            "perfil": safe_user_context.perfil,
            "condominio_id": safe_user_context.condominio_id,
            "agents_called": [],
            "latencies_ms": {},
            "sources": [],
            "corrections": 0,
        }

    async def _finish(self, session_id: str, user_id: int, state: dict[str, Any]) -> str:
        answer = state.get("answer", "Não foi possível gerar uma resposta validada.")
        await self.sessions.append_message(session_id, user_id, "assistant", answer)
        await self.sessions.update_last_route(
            session_id,
            user_id,
            state.get("route", state.get("agent", "guardrail")),
        )
        return answer

    def _response(
        self,
        *,
        session_id: str,
        request_id: str,
        state: dict[str, Any],
        answer: str,
        started: float,
        trace: ConversationTrace,
        quota_status: QuotaStatus | None,
    ) -> ChatResponse:
        agents_called = state.get("agents_called", [])
        AGENTS_CALLED.observe(len(agents_called))
        TOKENS_PER_CHAT.observe(trace.usage.total_tokens)
        judge = state.get("judge")
        record_chat(
            self.graph.settings,
            trace.usage.input_tokens,
            trace.usage.output_tokens,
            resolved=bool(judge and judge.get("aprovado")),
        )
        return ChatResponse(
            session_id=session_id,
            request_id=request_id,
            agent=state.get("agent", "guardrail"),
            answer=answer,
            sources=state.get("sources", []),
            agents_called=agents_called,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            judge=JudgeDecisionResponse(**judge) if judge else None,
            usage=TokenUsageResponse(**trace.usage.as_dict()),
            quota=QuotaInfo.from_status(quota_status) if quota_status else None,
        )

    async def _refund(self, quota_status: QuotaStatus | None) -> None:
        if self.quotas is not None and quota_status is not None:
            await self.quotas.refund_daily(quota_status)

    async def _recheck_session(self, user_context: UserContext, session_id: str) -> None:
        """Revalida o teto da sessão JÁ com o lock da sessão.

        `admit` checa antes do lock para responder 429 sem custo, mas duas
        requisições simultâneas na mesma sessão passariam ambas por ali e a
        segunda acionaria o resumo de memória. Aqui, dentro do lock, a
        contagem é a definitiva."""
        if self.quotas is None:
            return
        session = await self.sessions.get_session(session_id, user_context.user_id)
        self.quotas.check_session(user_context, session)

    async def remember(self, user_id: int, session_id: str, *, force: bool = False) -> None:
        """Consolida na memória de longo prazo o que a sessão trouxe de novo.

        Melhor esforço, depois da resposta: uma falha aqui nunca afeta o chat.
        """
        settings = self.graph.settings
        if not settings.long_term_memory_enabled:
            return
        try:
            async with self._remember_locks.setdefault(user_id, asyncio.Lock()):
                session = await self.sessions.get_session(session_id, user_id)
                desde = session.get("memoria_longa_ate")
                novas = [
                    message
                    for message in session.get("messages", [])
                    if str(message.get("role", "")).lower() in {"user", "assistant"}
                    and (desde is None or message["created_at"] > desde)
                ]
                turnos = sum(1 for message in novas if str(message.get("role", "")).lower() == "user")
                if turnos == 0 or (not force and turnos < settings.long_term_memory_every_turns):
                    return
                # O que o guardrail bloquearia nunca vira memória permanente.
                seguras = [
                    message
                    for message in novas
                    if str(message.get("role", "")).lower() != "user"
                    or (not message.get("bloqueada") and validate_input(str(message.get("content", ""))).allowed)
                ]
                if not any(str(m.get("role", "")).lower() == "user" for m in seguras):
                    await self.sessions.mark_long_term_memory(session_id, user_id, novas[-1]["created_at"])
                    return
                atual = await self.sessions.get_user_memory(user_id)
                texto = await self.graph.memory_summarizer.consolidate_user_memory(atual["texto"], seguras)
                texto = sanitize_output(texto).strip()[: settings.long_term_memory_max_chars]
                if not validate_input(texto).allowed:
                    logger.warning("memoria_longo_prazo_descartada", extra={"usuario_id": user_id})
                    # Não grava, mas avança a marca: a mesma janela não é reprocessada a cada turno.
                    await self.sessions.mark_long_term_memory(session_id, user_id, novas[-1]["created_at"])
                    return
                if await self.sessions.save_user_memory(user_id, texto, expected_revision=atual["revision"]):
                    await self.sessions.mark_long_term_memory(session_id, user_id, novas[-1]["created_at"])
        except Exception:
            logger.warning("memoria_longo_prazo_falhou", exc_info=True, extra={"usuario_id": user_id})

    # ------------------------------------------------------------------ #
    # /chat
    # ------------------------------------------------------------------ #

    async def chat(
        self,
        request: ChatRequest,
        *,
        request_id: str,
        user_context: UserContext,
    ) -> ChatResponse:
        started = time.perf_counter()
        user_id = user_context.user_id
        session, quota_status = await self.admit(request, user_context)
        session_id = session["session_id"]

        try:
            # Uma sessão é processada em ordem no mesmo runtime. Isso evita duas
            # respostas simultâneas reordenarem append/compactação da memória.
            async with self.sessions.conversation_guard(session_id):
                await self._recheck_session(user_context, session_id)
                message_id = await self.sessions.append_message(session_id, user_id, "user", request.mensagem)
                with conversation_trace(
                    session_id=session_id,
                    user_id=user_id,
                    request_id=request_id,
                    perfil=user_context.perfil,
                ) as trace, authenticated_request_context(user_context.token):
                    state = await self.graph.invoke(
                        self._initial_state(request, request_id, session_id, user_context)
                    )
                if state.get("blocked"):
                    await self.sessions.flag_message_blocked(session_id, user_id, message_id)
                answer = await self._finish(session_id, user_id, state)
        except Exception:
            # Erro do servidor (ou teto da sessão atingido dentro do lock) não
            # consome a cota do usuário.
            await self._refund(quota_status)
            raise

        return self._response(
            session_id=session_id,
            request_id=request_id,
            state=state,
            answer=answer,
            started=started,
            trace=trace,
            quota_status=quota_status,
        )

    # ------------------------------------------------------------------ #
    # /chat/stream (SSE)
    # ------------------------------------------------------------------ #

    async def stream(
        self,
        request: ChatRequest,
        *,
        request_id: str,
        user_context: UserContext,
        session: dict[str, Any],
        quota_status: QuotaStatus | None,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Gera eventos (nome, payload) a partir de uma sessão já admitida.

        Não há streaming token a token de propósito: toda resposta passa pelo
        juiz de saída e pelo guardrail antes de ser exibida. Transmitir os
        tokens do especialista enquanto são gerados mostraria ao usuário texto
        que ainda pode ser censurado ou reprovado. O stream mostra o progresso
        real do pipeline e entrega a resposta já validada.
        """
        started = time.perf_counter()
        user_id = user_context.user_id
        session_id = session["session_id"]
        yield "session", {"session_id": session_id, "request_id": request_id}

        state: dict[str, Any] = {}
        last_label = None
        try:
            async with self.sessions.conversation_guard(session_id):
                await self._recheck_session(user_context, session_id)
                message_id = await self.sessions.append_message(session_id, user_id, "user", request.mensagem)
                with conversation_trace(
                    session_id=session_id,
                    user_id=user_id,
                    request_id=request_id,
                    perfil=user_context.perfil,
                ) as trace, authenticated_request_context(user_context.token):
                    # aclosing: se o cliente desconectar, o stream do grafo é
                    # fechado junto (sem tarefas do LangGraph órfãs).
                    async with aclosing(
                        self.graph.astream(self._initial_state(request, request_id, session_id, user_context))
                    ) as events:
                        async for mode, chunk in events:
                            if mode == "values":
                                state = chunk
                                continue
                            for node in chunk:
                                label = _PROGRESS_LABELS.get(node)
                                if label and label != last_label:
                                    last_label = label
                                    yield "progress", {"step": node, "label": label}
                if state.get("blocked"):
                    await self.sessions.flag_message_blocked(session_id, user_id, message_id)
                answer = await self._finish(session_id, user_id, state)
        except QuotaExceeded as exc:
            await self._refund(quota_status)
            yield "error", {"codigo": f"limite_{exc.kind}", "detail": exc.message}
            return
        except SessionBusy as exc:
            await self._refund(quota_status)
            yield "error", {"codigo": "sessao_ocupada", "detail": str(exc)}
            return
        except Exception:
            await self._refund(quota_status)
            yield "error", {"codigo": "erro_interno", "detail": "Não foi possível concluir a resposta. Tente novamente."}
            return

        response = self._response(
            session_id=session_id,
            request_id=request_id,
            state=state,
            answer=answer,
            started=started,
            trace=trace,
            quota_status=quota_status,
        )
        yield "answer", {"answer": answer}
        yield "done", response.model_dump(mode="json")
