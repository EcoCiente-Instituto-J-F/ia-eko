from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncIterator

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.agents.coleta.agent import CollectionAgent
from src.agents.factory import AgentSuite
from src.agents.state import EcoState
from src.core.config import Settings
from src.agents.output_parsing import parse_input_judge
from src.observability.metrics import (
    AGENT_LATENCY,
    GUARDRAIL_BLOCKED,
    INPUT_JUDGE_DECISIONS,
    JUDGE_DECISIONS,
    JUDGE_REJECTED,
    ROUTING_DECISIONS,
    safe_label,
)
from src.observability.tracing import TracingService
from src.security.policies import authorize_route
from src.shared.context import UserContext
from src.services.rag_service import RAGService
from src.integrations.mcp.client import CalendarMcpClient
from src.services.session_service import SessionService
from src.services.memory_summarizer_service import MemorySummarizerService
from src.services.qdrant_service import QdrantFaqService
from src.api.schemas.common import SourceResponse
from src.security.guardrails import sanitize_output, validate_input

logger = logging.getLogger("ecociente.graph")


class EcoGraphRuntime:
    def __init__(
        self,
        settings: Settings,
        sessions: SessionService,
        rag: RAGService,
        calendar_mcp: CalendarMcpClient,
        tracing: TracingService | None = None,
        faq_search: QdrantFaqService | None = None,
    ):
        self.settings = settings
        self.sessions = sessions
        self.rag = rag
        self.tracing = tracing or TracingService(settings)
        self.agents = AgentSuite(settings, self.tracing)
        self.memory_summarizer = MemorySummarizerService(settings, model=self.agents.model, tracing=self.tracing)
        self.collection_agent = CollectionAgent(calendar_mcp)
        self.faq_search = faq_search
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
        # Antes: `"status=bloqueado" in texto` — nunca casava com o JSON que o
        # prompt pede ("status": "bloqueado"), então o juiz LLM não bloqueava nada.
        input_decision = parse_input_judge(judge_text)
        INPUT_JUDGE_DECISIONS.labels(
            outcome="blocked" if input_decision.blocked else "approved",
            category=input_decision.category if input_decision.blocked or input_decision.category == "saida_invalida" else "aprovado",
        ).inc()
        if input_decision.blocked:
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

    @classmethod
    def build_orchestrator_prompt(
        cls,
        mensagem: str,
        user_context: UserContext | None,
        memory_context: dict[str, Any] | None = None,
    ) -> str:
        """Prompt do orquestrador. Público porque os evals de roteamento
        (`evals/routing`) precisam montar exatamente o mesmo prompt da produção."""
        safe_user_context = user_context.for_agent() if user_context else {}
        return (
            f"MENSAGEM_ORIGINAL={mensagem}\n"
            f"CONTEXTO_IDENTIDADE={json.dumps(safe_user_context, ensure_ascii=False, default=str)}\n"
            f"CONTEXTO_MEMORIA=\n{cls._format_memory_context(memory_context or {})}"
        )

    async def orquestrador(self, state: EcoState) -> dict[str, Any]:
        started = time.perf_counter()
        user_context = state.get("user_context")
        prompt = self.build_orchestrator_prompt(state["mensagem"], user_context, state.get("memory_context", {}))
        text = await self.agents.invoke("orquestrador", prompt)
        route = self.agents.parse_route(text)
        ROUTING_DECISIONS.labels(route=route).inc()
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
        if self.settings.faq_backend == "qdrant" and self.faq_search is not None:
            return await self._faq_qdrant(state)
        return await self._rag_specialist(state, "faq")

    async def _faq_qdrant(self, state: EcoState) -> dict[str, Any]:
        """FAQ por busca vetorial: devolve a resposta canônica, sem LLM.

        Sem resultado acima de QDRANT_MIN_SCORE (ou Qdrant fora do ar), usa o
        RAG+LLM antigo só se FAQ_LLM_FALLBACK=true."""
        started = time.perf_counter()
        user_context = state.get("user_context")
        perfil = user_context.perfil if user_context else None
        melhor = None
        try:
            faqs = await self.faq_search.buscar_faq(state["mensagem"])
            melhor = self.faq_search.melhor_resposta(faqs, perfil)
        except Exception:
            logger.warning("faq_qdrant_falhou", exc_info=True)
            if self.settings.faq_llm_fallback:
                return await self._rag_specialist(state, "faq")
            return {
                **self._mark(state, "faq", started),
                "candidate_answer": "A base de perguntas frequentes está indisponível no momento. Tente novamente em instantes.",
                "sources": [],
                "agent": "faq",
                "faq_canonica": True,
            }

        if melhor is None:
            if self.settings.faq_llm_fallback:
                return await self._rag_specialist(state, "faq")
            return {
                **self._mark(state, "faq", started),
                "candidate_answer": (
                    "Não encontrei essa resposta nas perguntas frequentes do EcoCiente. "
                    "Tente reformular a pergunta ou fale com o síndico do seu condomínio."
                ),
                "sources": [],
                "agent": "faq",
                "faq_canonica": True,
            }

        source = SourceResponse(
            title=str(melhor.get("titulo") or "FAQ EcoCiente"),
            source=f"qdrant:{self.faq_search.collection_name}/{melhor.get('faq_id')}",
        )
        return {
            **self._mark(state, "faq", started),
            "candidate_answer": str(melhor["resposta_canonica"]).strip(),
            "sources": [source],
            "agent": "faq",
            "faq_canonica": True,
        }

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

    async def grafo(self, state: EcoState) -> dict[str, Any]:
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
            "Use exclusivamente ferramentas autorizadas e nunca invente uma conexão que a tool não confirmou."
        )
        answer = await self.agents.invoke("grafo", prompt)
        return {
            **self._mark(state, "grafo", started),
            "candidate_answer": answer,
            "sources": [],
            "agent": "grafo",
        }

    async def juiz_saida(self, state: EcoState) -> dict[str, Any]:
        started = time.perf_counter()
        candidate = state.get("candidate_answer", "")
        if state.get("faq_canonica"):
            # Resposta canônica do Qdrant: texto curado e já revisado na base, não
            # gerado por LLM. Passar pelo juiz LLM anularia a economia da busca.
            decision = {
                "aprovado": bool(candidate.strip()),
                "motivo": "resposta_canonica_qdrant",
                "categoria": "aprovado" if candidate.strip() else "resposta_vazia",
                "necessita_correcao": False,
                "resposta_censurada": None,
            }
        elif state.get("route") == "coletas":
            # A resposta é derivada diretamente da API externa; evitar uma
            # segunda execução por correção impede mutações duplicadas.
            decision = {
                "aprovado": bool(candidate.strip()),
                "motivo": "resposta_verificada_por_calendario",
                "categoria": "aprovado" if candidate.strip() else "resposta_vazia",
                "necessita_correcao": False,
                "resposta_censurada": None,
            }
        elif not candidate.strip():
            decision = {
                "aprovado": False,
                "motivo": "resposta_vazia",
                "categoria": "resposta_vazia",
                "necessita_correcao": True,
                "resposta_censurada": None,
            }
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
        category = safe_label(decision.get("categoria") or "outro")
        JUDGE_DECISIONS.labels(
            agent=state.get("route", "desconhecido"),
            outcome="approved" if decision["aprovado"] else "rejected",
            category=category,
        ).inc()
        if not decision["aprovado"]:
            # Antes o label era o motivo em texto livre do LLM (cardinalidade
            # ilimitada no Prometheus). Agora é a categoria fechada.
            JUDGE_REJECTED.labels(reason=category).inc()
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
        elif route == "grafo":
            sources = state.get("sources", [])
            candidate = (
                "Não foi possível validar a resposta sobre relacionamentos com segurança. "
                "Nenhuma conexão será apresentada sem confirmação pela camada de grafo (Neo4j)."
            )
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
        judge = state.get("judge") or {}
        # `aprovado_com_censura`: o juiz removeu dado de terceiro/afirmação sem
        # base. Exibir `candidate_answer` aqui vazaria exatamente o que foi censurado.
        censored = judge.get("resposta_censurada") if judge.get("aprovado") else None
        answer = sanitize_output(censored or state.get("candidate_answer", ""))
        if judge and not judge.get("aprovado", True) and int(state.get("corrections", 0)) >= self.settings.judge_max_corrections:
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
        builder.add_node("grafo", self.grafo)
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
                "grafo": "grafo",
                "blocked_authorization": "guardrail_saida",
            },
        )
        for specialist in ("faq", "analytics", "educacional", "coletas", "grafo"):
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

    # O checkpointer usa thread_id = session_id, então o estado de um turno
    # sobrevive para o próximo. Campos que pertencem a UM turno precisam ser
    # zerados na entrada; sem isso, uma mensagem bloqueada no turno 2 exibia
    # a `resposta_censurada` do juiz do turno 1.
    _TURN_DEFAULTS: dict[str, Any] = {
        "judge": {},
        "candidate_answer": "",
        "answer": "",
        "sources": [],
        "blocked": False,
        "blocked_reason": "",
        "corrections": 0,
        "memory_compaction_error": None,
        "faq_canonica": False,
    }

    def _turn_state(self, initial_state: EcoState) -> EcoState:
        return {**self._TURN_DEFAULTS, **initial_state}  # type: ignore[return-value]

    async def invoke(self, initial_state: EcoState) -> EcoState:
        config = {"configurable": {"thread_id": initial_state["session_id"]}}
        return await self.graph.ainvoke(self._turn_state(initial_state), config=config)

    async def astream(self, initial_state: EcoState) -> AsyncIterator[tuple[str, Any]]:
        """Executa o mesmo grafo emitindo ("updates", {nó: parcial}) a cada nó
        concluído e ("values", estado) com o estado acumulado. Usado pelo SSE."""
        config = {"configurable": {"thread_id": initial_state["session_id"]}}
        async for mode, chunk in self.graph.astream(
            self._turn_state(initial_state),
            config=config,
            stream_mode=["updates", "values"],
        ):
            yield mode, chunk
