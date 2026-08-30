from __future__ import annotations

import re
import time
from typing import Any

from src.core.config import Settings
from src.core.llm import build_chat_model
from src.observability.metrics import LLM_LATENCY, TOOL_LATENCY


class AgentSuite:
    """Cria agentes LangChain em produção e equivalentes determinísticos no modo mock."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = build_chat_model(settings)
        self.agents: dict[str, Any] = {}
        if settings.llm_provider != "mock":
            self._build_langchain_agents()

    def _build_langchain_agents(self) -> None:
        from langchain.agents import create_agent

        from src.agents.analytics.tools import TOOLS as ANALYTICS_TOOLS
        from src.prompts.agents.analytics import ANALYTICS_PROMPT_COMPLETO
        from src.prompts.agents.educacional import EDUCADOR_PROMPT_COMPLETO
        from src.prompts.agents.faq import FAQ_PROMPT_COMPLETO
        from src.prompts.shared.judges import JUIZ_ENTRADA_PROMPT_COMPLETO, JUIZ_SAIDA_PROMPT_COMPLETO
        from src.prompts.shared.orchestrator import ORQUESTRADOR_PROMPT_COMPLETO

        assert self.model is not None
        definitions = {
            "juiz_entrada": (JUIZ_ENTRADA_PROMPT_COMPLETO, []),
            "orquestrador": (ORQUESTRADOR_PROMPT_COMPLETO, []),
            "faq": (FAQ_PROMPT_COMPLETO, []),
            "analytics": (ANALYTICS_PROMPT_COMPLETO, ANALYTICS_TOOLS),
            "educacional": (EDUCADOR_PROMPT_COMPLETO, []),
            "juiz_saida": (JUIZ_SAIDA_PROMPT_COMPLETO, []),
        }
        for name, (system_prompt, tools) in definitions.items():
            self.agents[name] = create_agent(
                model=self.model,
                tools=tools,
                system_prompt=system_prompt,
                name=f"ecociente_{name}",
            )

    async def invoke(self, agent_name: str, prompt: str) -> str:
        if self.settings.llm_provider == "mock":
            return self._mock_response(agent_name, prompt)
        agent = self.agents[agent_name]
        started = time.perf_counter()
        result = await agent.ainvoke({"messages": [{"role": "user", "content": prompt}]})
        LLM_LATENCY.labels(provider=self.settings.llm_provider, agent=agent_name).observe(
            time.perf_counter() - started
        )
        messages = result.get("messages", []) if isinstance(result, dict) else []
        if not messages:
            return str(result)
        content = messages[-1].content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("text"):
                    parts.append(str(part["text"]))
                else:
                    parts.append(str(part))
            return "\n".join(parts)
        return str(content)

    async def run_analytics(self, prompt: str) -> str:
        if self.settings.llm_provider == "mock":
            return self._mock_response("analytics", prompt)
        started = time.perf_counter()
        result = await self.invoke("analytics", prompt)
        TOOL_LATENCY.labels(tool="postgresql_analytics_agent").observe(time.perf_counter() - started)
        return result

    @staticmethod
    def parse_route(text: str) -> str:
        match = re.search(r"ROUTE\s*=\s*(coletas|educador|educacional|analytics|faq)", text, re.I)
        if not match:
            return "faq"
        route = match.group(1).lower()
        return "educacional" if route == "educador" else route

    @staticmethod
    def parse_judge(text: str) -> dict[str, Any]:
        lowered = text.lower()
        approved = bool(re.search(r"status\s*=\s*aprovado", lowered))
        reason_match = re.search(r"motivo\s*=\s*(.+)", text, re.I)
        reason = reason_match.group(1).strip() if reason_match else ("aprovado" if approved else "Resposta não aprovada pelo juiz.")
        return {
            "aprovado": approved,
            "motivo": reason[:500],
            "necessita_correcao": not approved,
        }

    def _mock_response(self, agent_name: str, prompt: str) -> str:
        lower = prompt.lower()
        if agent_name == "juiz_entrada":
            return "STATUS=aprovado\nMENSAGEM_ORIGINAL=[mantida]"
        if agent_name == "orquestrador":
            if any(w in lower for w in ["ranking", "desempenho", "mais reciclado", "estatística", "estatistica", "dashboard", "top 10", "evoluiu"]):
                route = "analytics"
            elif any(w in lower for w in ["coleta", "calendário", "calendario", "cooperativa", "agendamento", "recorrência", "recorrencia"]):
                route = "coletas"
            elif any(w in lower for w in ["reciclável", "reciclavel", "compost", "separar", "descarte", "sustentabilidade", "resíduo", "residuo"]):
                route = "educador"
            else:
                route = "faq"
            return f"ROUTE={route}\nPERGUNTA_ORIGINAL=[mantida]"
        if agent_name == "analytics":
            return (
                "No modo mock não consulto dados reais de PostgreSQL/Redis. "
                "A rota Analytics foi selecionada corretamente; conecte os bancos para obter métricas reais."
            )
        if agent_name in {"faq", "educacional", "coletas"}:
            marker = "CONTEXTO_RAG:\n"
            context = prompt.split(marker, 1)[-1] if marker in prompt else ""
            first = " ".join(context.split())[:650]
            if first:
                return f"Com base na base consultada: {first}"
            return "A base consultada não contém evidência suficiente para responder com segurança."
        if agent_name == "juiz_saida":
            return "STATUS=aprovado\nESPECIALISTA_JSON=[resposta aprovada]"
        return ""
