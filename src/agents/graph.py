from __future__ import annotations

import json
import logging
import time
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.agents.coleta.agent import CollectionAgent
from src.agents.factory import AgentSuite
from src.agents.state import EcoState
from src.core.config import Settings
from src.observability.metrics import AGENT_LATENCY, GUARDRAIL_BLOCKED, JUDGE_REJECTED, safe_label
from src.security.policies import authorize_route
from src.services.rag_service import RAGService
from src.integrations.mcp.client import CalendarMcpClient
from src.services.session_service import SessionService
from src.services.memory_summarizer_service import MemorySummarizerService
from src.security.guardrails import sanitize_output, validate_input

logger = logging.getLogger("ecociente.graph")


class EcoGraphRuntime:
    def __init__(
        self,
        settings: Settings,
        sessions: SessionService,
        rag: RAGService,
        calendar_mcp: CalendarMcpClient,
    ):
        self.settings = settings
        self.sessions = sessions
        self.rag = rag
        self.agents = AgentSuite(settings)
        self.memory_summarizer = MemorySummarizerService(settings, model=self.agents.model)
        self.collection_agent = CollectionAgent(calendar_mcp)
        self.graph = self._build_graph()

    @staticmethod
    def _mark(state: EcoState, name: str, started: float) -> dict[str, Any]:
        duration_ms = (time.perf_counter() - started) * 1000
        AGENT_LATENCY.labels(agent=name).observe(duration_ms / 1000)
        called = list(state.get("agents_called", []))
        called.append(name)
        latencies = dict(state.get("latencies_ms", {}))
        latencies[name] = round(duration_ms, 2)
        return {"agents_called": called, "latencies_ms": latencies}

    async def guardrail_entrada(self, state: EcoState) -> dict[str, Any]:
        started = time.perf_counter()
        deterministic = validate_input(state["mensagem"])
        if not deterministic.allowed:
            GUARDRAIL_BLOCKED.labels(reason=deterministic.reason).inc()
            update = self._mark(state, "guardrail_entrada", started)
            return {
                **update,
                "blocked": True,
                "blocked_reason": deterministic.reason,
                "candidate_answer": deterministic.safe_message or "Solicitação bloqueada por segurança.",
                "agent": "guardrail_entrada",
            }
        judge_text = await self.agents.invoke(
            "juiz_entrada",
            f"MENSAGEM_ORIGINAL={state['mensagem']}\nPERFIL={state.get('perfil', 'nao_informado')}",
        )
        if "status=bloqueado" in judge_text.lower():
            reason = "juiz_entrada_llm"
            GUARDRAIL_BLOCKED.labels(reason=reason).inc()
            update = self._mark(state, "guardrail_entrada", started)
            return {
                **update,
                "blocked": True,
                "blocked_reason": reason,
                "candidate_answer": "Não posso atender essa solicitação dentro das regras de segurança e privacidade do EcoCiente.",
                "agent": "juiz_entrada",
            }
        update = self._mark(state, "guardrail_entrada", started)
        return {**update, "blocked": False}

    @staticmethod
    def _format_memory_context(memory: dict[str, Any]) -> str:
        summary = str(memory.get("memory_summary", "") or "").strip() or "Nenhuma."
        recent = memory.get("recent_messages", []) or []
        lines = []
        for message in recent:
            role = str(message.get("role", "")).lower()
            if role not in {"user", "assistant"}:
                continue
            label = "User" if role == "user" else "Assistant"
            lines.append(f"{label}: {message.get('content', '')}")
        conversation = "\n".join(lines) if lines else "Nenhuma mensagem anterior na janela recente."
        return f"MEMÓRIA CONSOLIDADA:\n{summary}\n\nCONVERSA RECENTE:\n{conversation}"

    async def memoria(self, state: EcoState) -> dict[str, Any]:
        started = time.perf_counter()
        memory = await self.sessions.get_memory_context(
            state["session_id"],
            state["usuario_id"],
            current_user_message=state["mensagem"],
        )
        return {**self._mark(state, "memoria", started), "memory_context": memory}

    async def verificar_compactacao(self, state: EcoState) -> dict[str, Any]:
        started = time.perf_counter()
        needed = self.sessions.needs_compaction(state.get("memory_context", {}))
        return {
            **self._mark(state, "verificar_compactacao", started),
            "memory_compaction_needed": needed,
        }

    async def resumir_memoria(self, state: EcoState) -> dict[str, Any]:
        started = time.perf_counter()
        error: str | None = None
        try:
            await self.sessions.compact_memory_if_needed(
                state["session_id"],
                state["usuario_id"],
                self.memory_summarizer,
            )
        except Exception as exc:
            # Falha explícita: o SessionService só persiste depois de obter um
            # resumo completo, portanto o histórico permanece intacto.
            logger.exception(
                "memory_compaction_failed",
                extra={"session_id": state["session_id"], "usuario_id": state["usuario_id"]},
            )
            error = f"{type(exc).__name__}: {exc}"
        memory = await self.sessions.get_memory_context(
            state["session_id"],
            state["usuario_id"],
            current_user_message=state["mensagem"],
        )
        return {
            **self._mark(state, "resumir_memoria", started),
            "memory_context": memory,
            "memory_compaction_error": error,
        }

    @staticmethod
    def _after_compaction_check(state: EcoState) -> str:
        return "compact" if state.get("memory_compaction_needed") else "continue"

    async def orquestrador(self, state: EcoState) -> dict[str, Any]:
        started = time.perf_counter()
        user_context = state.get("user_context")
        safe_user_context = user_context.for_agent() if user_context else {}
        prompt = (
            f"MENSAGEM_ORIGINAL={state['mensagem']}\n"
            f"CONTEXTO_IDENTIDADE={json.dumps(safe_user_context, ensure_ascii=False, default=str)}\n"
            f"CONTEXTO_MEMORIA=\n{self._format_memory_context(state.get('memory_context', {}))}"
        )
        text = await self.agents.invoke("orquestrador", prompt)
        route = self.agents.parse_route(text)
        decision = authorize_route(user_context, route, state["mensagem"])
        if not decision.allowed:
            return {
                **self._mark(state, "orquestrador", started),
                "route": "blocked_authorization",
                "agent": "autorizacao",
                "candidate_answer": decision.message,
                "sources": [],
            }
        return {**self._mark(state, "orquestrador", started), "route": route, "agent": route}

    async def _rag_specialist(self, state: EcoState, name: str) -> dict[str, Any]:
        started = time.perf_counter()
        user_context = state.get("user_context")
        safe_user_context = user_context.for_agent() if user_context else {}
        context, sources = await self.rag.search(state["mensagem"])
        if not context:
            answer = "A base consultada não contém evidência suficiente para responder com segurança."
        else:
            prompt = (
                f"PERGUNTA_ORIGINAL={state['mensagem']}\n"
                f"CONTEXTO_IDENTIDADE={json.dumps(safe_user_context, ensure_ascii=False, default=str)}\n"
                f"CONTEXTO_MEMORIA=\n{self._format_memory_context(state.get('memory_context', {}))}\n"
                f"CONTEXTO_RAG:\n{context}\n\n"
                "Responda somente com fatos sustentados pelo CONTEXTO_RAG; se faltar evidência, declare a insuficiência."
            )
            answer = await self.agents.invoke(name, prompt)
        return {
            **self._mark(state, name, started),
            "candidate_answer": answer,
            "sources": sources,
            "agent": name,
        }

    async def faq(self, state: EcoState) -> dict[str, Any]:
        return await self._rag_specialist(state, "faq")

    async def educacional(self, state: EcoState) -> dict[str, Any]:
        return await self._rag_specialist(state, "educacional")

    async def coletas(self, state: EcoState) -> dict[str, Any]:
        started = time.perf_counter()
        user_context = state.get("user_context")
        if user_context is None:
            answer = "Não foi possível validar o contexto de acesso para consultar o calendário."
        else:
            result = await self.collection_agent.run(state["mensagem"], user_context)
            answer = result.answer
        return {
            **self._mark(state, "coletas", started),
            "candidate_answer": answer,
            "sources": [],
            "agent": "coletas",
        }

    async def analytics(self, state: EcoState) -> dict[str, Any]:
        started = time.perf_counter()
        user_context = state.get("user_context")
        safe_user_context = user_context.for_agent() if user_context else {}
        prompt = (
            f"PERGUNTA_ORIGINAL={state['mensagem']}\n"
            f"USUARIO_ID={state['usuario_id']}\n"
            f"PERFIL={state.get('perfil', 'nao_informado')}\n"
            f"CONDOMINIO_ID={state.get('condominio_id')}\n"
            f"CONTEXTO_IDENTIDADE={json.dumps(safe_user_context, ensure_ascii=False, default=str)}\n"
            f"CONTEXTO_MEMORIA=\n{self._format_memory_context(state.get('memory_context', {}))}\n"
            "Use exclusivamente ferramentas autorizadas e não invente números quando a consulta falhar."
        )
        answer = await self.agents.run_analytics(prompt)
        return {
            **self._mark(state, "analytics", started),
            "candidate_answer": answer,
            "sources": [],
            "agent": "analytics",
        }

    async def juiz_saida(self, state: EcoState) -> dict[str, Any]:
        started = time.perf_counter()
        candidate = state.get("candidate_answer", "")
        if state.get("route") == "coletas":
            # A resposta é derivada diretamente da API externa; evitar uma
            # segunda execução por correção impede mutações duplicadas.
            decision = {
                "aprovado": bool(candidate.strip()),
                "motivo": "resposta_verificada_por_calendario",
                "necessita_correcao": False,
            }
        elif not candidate.strip():
            decision = {"aprovado": False, "motivo": "resposta_vazia", "necessita_correcao": True}
        else:
            source_info = [s.model_dump() for s in state.get("sources", [])]
            prompt = (
                f"ROTA={state.get('route')}\n"
                f"PERGUNTA={state['mensagem']}\n"
                f"FONTES={json.dumps(source_info, ensure_ascii=False)}\n"
                f"ESPECIALISTA_JSON={json.dumps({'resposta': candidate}, ensure_ascii=False)}"
            )
            raw = await self.agents.invoke("juiz_saida", prompt)
            decision = self.agents.parse_judge(raw)
        if not decision["aprovado"]:
            JUDGE_REJECTED.labels(reason=safe_label(decision["motivo"])).inc()
        return {**self._mark(state, "juiz_saida", started), "judge": decision}

    async def correcao(self, state: EcoState) -> dict[str, Any]:
        started = time.perf_counter()
        corrections = int(state.get("corrections", 0)) + 1
        route = state.get("route", "faq")
        reason = state.get("judge", {}).get("motivo", "fundamentação insuficiente")
        if route in {"faq", "educacional"}:
            context, sources = await self.rag.search(state["mensagem"])
            prompt = (
                f"PERGUNTA_ORIGINAL={state['mensagem']}\nCONTEXTO_RAG:\n{context}\n"
                f"CORRECAO_EXIGIDA={reason}\nReescreva sem inventar fatos e somente com a evidência fornecida."
            )
            candidate = await self.agents.invoke(route, prompt) if context else "A base consultada não contém evidência suficiente para responder com segurança."
        else:
            sources = state.get("sources", [])
            candidate = (
                "Não foi possível validar a resposta analítica com segurança. "
                "Nenhum dado numérico será apresentado sem confirmação pelas ferramentas autorizadas."
            )
        return {
            **self._mark(state, "correcao", started),
            "candidate_answer": candidate,
            "sources": sources,
            "corrections": corrections,
        }

    async def guardrail_saida(self, state: EcoState) -> dict[str, Any]:
        started = time.perf_counter()
        answer = sanitize_output(state.get("candidate_answer", ""))
        if not state.get("judge", {}).get("aprovado", True) and int(state.get("corrections", 0)) >= self.settings.judge_max_corrections:
            answer = (
                "Não consegui validar uma resposta suficientemente fundamentada para esta solicitação. "
                "Tente reformular a pergunta ou verifique os dados/fontes disponíveis no EcoCiente."
            )
        return {**self._mark(state, "guardrail_saida", started), "answer": answer}

    def _after_input(self, state: EcoState) -> str:
        return "blocked" if state.get("blocked") else "continue"

    def _route(self, state: EcoState) -> str:
        return state.get("route", "faq")

    def _after_judge(self, state: EcoState) -> str:
        if state.get("judge", {}).get("aprovado", False):
            return "approved"
        if int(state.get("corrections", 0)) < self.settings.judge_max_corrections:
            return "correct"
        return "give_up"

    def _build_graph(self):
        builder = StateGraph(EcoState)
        builder.add_node("guardrail_entrada", self.guardrail_entrada)
        builder.add_node("memoria", self.memoria)
        builder.add_node("verificar_compactacao", self.verificar_compactacao)
        builder.add_node("resumir_memoria", self.resumir_memoria)
        builder.add_node("orquestrador", self.orquestrador)
        builder.add_node("faq", self.faq)
        builder.add_node("analytics", self.analytics)
        builder.add_node("educacional", self.educacional)
        builder.add_node("coletas", self.coletas)
        builder.add_node("juiz_saida", self.juiz_saida)
        builder.add_node("correcao", self.correcao)
        builder.add_node("guardrail_saida", self.guardrail_saida)

        builder.add_edge(START, "guardrail_entrada")
        builder.add_conditional_edges(
            "guardrail_entrada",
            self._after_input,
            {"blocked": "guardrail_saida", "continue": "memoria"},
        )
        builder.add_edge("memoria", "verificar_compactacao")
        builder.add_conditional_edges(
            "verificar_compactacao",
            self._after_compaction_check,
            {"compact": "resumir_memoria", "continue": "orquestrador"},
        )
        builder.add_edge("resumir_memoria", "orquestrador")
        builder.add_conditional_edges(
            "orquestrador",
            self._route,
            {
                "faq": "faq",
                "analytics": "analytics",
                "educacional": "educacional",
                "coletas": "coletas",
                "blocked_authorization": "guardrail_saida",
            },
        )
        for specialist in ("faq", "analytics", "educacional", "coletas"):
            builder.add_edge(specialist, "juiz_saida")
        builder.add_conditional_edges(
            "juiz_saida",
            self._after_judge,
            {"approved": "guardrail_saida", "correct": "correcao", "give_up": "guardrail_saida"},
        )
        builder.add_edge("correcao", "juiz_saida")
        builder.add_edge("guardrail_saida", END)

        # Checkpointer de workflow/thread. MongoDB continua sendo a memória conversacional persistente.
        return builder.compile(checkpointer=InMemorySaver())

    async def invoke(self, initial_state: EcoState) -> EcoState:
        config = {"configurable": {"thread_id": initial_state["session_id"]}}
        return await self.graph.ainvoke(initial_state, config=config)
