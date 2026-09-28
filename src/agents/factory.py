from __future__ import annotations

import re
import time
from typing import Any

from src.agents.output_parsing import parse_output_judge, parse_route
from src.core.config import Settings
from src.core.llm import build_chat_model
from src.observability.metrics import LLM_LATENCY, TOOL_LATENCY
from src.observability.tracing import TracingService, record_usage
from src.prompts.shared.temporal import linha_data_hora


class AgentSuite:
    """Cria agentes LangChain em produção e equivalentes determinísticos no modo mock."""

    def __init__(self, settings: Settings, tracing: TracingService | None = None):
        self.settings = settings
        self.tracing = tracing or TracingService(settings)
        self.model = build_chat_model(settings)
        self.agents: dict[str, Any] = {}
        if settings.llm_provider != "mock":
            self._build_langchain_agents()

    def _build_langchain_agents(self) -> None:
        from langchain.agents import create_agent

        from src.agents.analytics.tools import TOOLS as ANALYTICS_TOOLS
        from src.agents.graph_traversal.tools import TOOLS as GRAFO_TOOLS
        from src.prompts.agents.analytics import ANALYTICS_PROMPT_COMPLETO
        from src.prompts.agents.educacional import EDUCADOR_PROMPT_COMPLETO
        from src.prompts.agents.faq import FAQ_PROMPT_COMPLETO
        from src.prompts.agents.graph_traversal import GRAFO_PROMPT_COMPLETO
        from src.prompts.shared.judges import JUIZ_ENTRADA_PROMPT_COMPLETO, JUIZ_SAIDA_PROMPT_COMPLETO
        from src.prompts.shared.orchestrator import ORQUESTRADOR_PROMPT_COMPLETO

        assert self.model is not None
        definitions = {
            "juiz_entrada": (JUIZ_ENTRADA_PROMPT_COMPLETO, []),
            "orquestrador": (ORQUESTRADOR_PROMPT_COMPLETO, []),
            "faq": (FAQ_PROMPT_COMPLETO, []),
            "analytics": (ANALYTICS_PROMPT_COMPLETO, ANALYTICS_TOOLS),
            "educacional": (EDUCADOR_PROMPT_COMPLETO, []),
            "grafo": (GRAFO_PROMPT_COMPLETO, GRAFO_TOOLS),
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
        config = self.tracing.run_config(agent_name)
        # Data atual a cada chamada: o prompt de sistema é fixo desde o boot.
        content = f"{linha_data_hora(self.settings.quota_timezone)}\n\n{prompt}"
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": content}]},
            config=config or None,
        )
        LLM_LATENCY.labels(provider=self.settings.llm_provider, agent=agent_name).observe(
            time.perf_counter() - started
        )
        messages = result.get("messages", []) if isinstance(result, dict) else []
        record_usage(provider=self.settings.llm_provider, agent=agent_name, messages=messages)
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