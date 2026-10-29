from __future__ import annotations

import json
import time
from typing import Any

from src.core.config import Settings
from src.core.llm import build_chat_model
from src.observability.cost import record_extra_cost
from src.observability.metrics import LLM_LATENCY
from src.observability.tracing import TracingService, record_usage
from src.prompts.shared.memory import MEMORY_SUMMARIZER_PROMPT, USER_MEMORY_PROMPT


class MemorySummarizerService:
    """Consolida memória antiga; nunca responde diretamente ao usuário."""

    def __init__(self, settings: Settings, model: Any | None = None, tracing: TracingService | None = None):
        self.settings = settings
        self.model = model if model is not None else build_chat_model(settings)
        self.tracing = tracing or TracingService(settings)

    async def summarize(self, previous_summary: str | None, messages: list[dict[str, Any]]) -> str:
        if self.settings.llm_provider == "mock":
            return self._mock_summary(previous_summary, messages)
        if self.model is None:
            raise RuntimeError("Modelo de resumo de memória não configurado.")

        payload = {
            "resumo_anterior": previous_summary or "",
            "mensagens_a_compactar": [
                {"role": message.get("role"), "content": message.get("content", "")}
                for message in messages
            ],
        }
        started = time.perf_counter()
        result = await self.model.ainvoke(
            [
                {"role": "system", "content": MEMORY_SUMMARIZER_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            config=self.tracing.run_config("memory_summarizer") or None,
        )
        LLM_LATENCY.labels(provider=self.settings.llm_provider, agent="memory_summarizer").observe(
            time.perf_counter() - started
        )
        record_usage(provider=self.settings.llm_provider, agent="memory_summarizer", messages=[result])
        return (result.text if hasattr(result, "text") else str(result)).strip()

    async def consolidate_user_memory(self, current: str, messages: list[dict[str, Any]]) -> str:
        """Memória de longo prazo do usuário atualizada com as mensagens novas."""
        if self.settings.llm_provider == "mock":
            return self._mock_user_memory(current, messages)
        if self.model is None:
            raise RuntimeError("Modelo de memória não configurado.")
        payload = {
            "memoria_atual": current or "",
            "mensagens_novas": [
                {"role": message.get("role"), "content": message.get("content", "")} for message in messages
            ],
        }
        started = time.perf_counter()
        result = await self.model.ainvoke(
            [
                {"role": "system", "content": USER_MEMORY_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            config=self.tracing.run_config("memoria_longo_prazo") or None,
        )
        LLM_LATENCY.labels(provider=self.settings.llm_provider, agent="memoria_longo_prazo").observe(
            time.perf_counter() - started
        )
        usage = record_usage(provider=self.settings.llm_provider, agent="memoria_longo_prazo", messages=[result])
        # Roda fora de uma resposta do /chat: o custo entra no ledger sem contar como solicitação.
        record_extra_cost(self.settings, usage.input_tokens, usage.output_tokens)
        return (result.text if hasattr(result, "text") else str(result)).strip()

    @staticmethod
    def _mock_user_memory(current: str, messages: list[dict[str, Any]]) -> str:
        lines = [line for line in (current or "").splitlines() if line.strip()]
        for message in messages:
            if str(message.get("role", "")).lower() != "user":
                continue
            content = " ".join(str(message.get("content", "")).split())
            if content:
                lines.append(f"- {content}")
        return "\n".join(lines)

    @staticmethod
    def _mock_summary(previous_summary: str | None, messages: list[dict[str, Any]]) -> str:
        chunks: list[str] = []
        if previous_summary:
            chunks.append(previous_summary.strip())
        for message in messages:
            role = str(message.get("role", "")).lower()
            if role not in {"user", "assistant"}:
                continue
            label = "Usuário" if role == "user" else "Assistente"
            content = " ".join(str(message.get("content", "")).split())
            if content:
                chunks.append(f"{label}: {content}")
        return " | ".join(chunks).strip()
