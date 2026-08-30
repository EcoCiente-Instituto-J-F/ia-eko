from __future__ import annotations

import json
import time
from typing import Any

from src.core.config import Settings
from src.core.llm import build_chat_model
from src.observability.metrics import LLM_LATENCY
from src.prompts.shared.memory import MEMORY_SUMMARIZER_PROMPT


class MemorySummarizerService:
    """Consolida memória antiga; nunca responde diretamente ao usuário."""

    def __init__(self, settings: Settings, model: Any | None = None):
        self.settings = settings
        self.model = model if model is not None else build_chat_model(settings)

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
            ]
        )
        LLM_LATENCY.labels(provider=self.settings.llm_provider, agent="memory_summarizer").observe(
            time.perf_counter() - started
        )
        content = getattr(result, "content", result)
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("text"):
                    parts.append(str(item["text"]))
                else:
                    parts.append(str(item))
            return "\n".join(parts).strip()
        return str(content).strip()

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
