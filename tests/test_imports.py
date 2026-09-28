"""Todo módulo de `src/` precisa importar.

A suíte roda com LLM_PROVIDER=mock, e nesse modo os prompts dos agentes nunca
são importados (só `_build_langchain_agents` os carrega). Assim, seis módulos
de prompt quebrados no import — f-strings com JSON literal, `{ "status": ...}`
lido como placeholder — passavam em todos os testes e derrubavam a API na
inicialização com qualquer LLM real (Gemini, Groq, Ollama).
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
MODULES = sorted(
    ".".join(path.relative_to(SRC.parent).with_suffix("").parts)
    for path in SRC.rglob("*.py")
    if "__pycache__" not in path.parts and path.name != "__init__.py"
)
# Dependências opcionais (requirements-optional.txt).
OPTIONAL = {"langchain_groq", "langchain_google_genai", "langfuse", "qdrant_client", "fastembed"}


@pytest.mark.parametrize("module", MODULES)
def test_module_imports(module: str) -> None:
    try:
        importlib.import_module(module)
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.split(".")[0] in OPTIONAL:
            pytest.skip(f"dependência opcional ausente: {exc.name}")
        raise


def test_prompts_render_literal_json_and_placeholders() -> None:
    from src.prompts.agents.analytics import ANALYTICS_PROMPT_COMPLETO
    from src.prompts.shared.judges import JUIZ_ENTRADA_PROMPT_COMPLETO
    from src.prompts.shared.orchestrator import ORQUESTRADOR_MEMORY_TOOL, ORQUESTRADOR_PROMPT_COMPLETO
    from src.prompts.shared.persona import PERSONA_SISTEMA

    assert PERSONA_SISTEMA.strip()[:40] in ORQUESTRADOR_PROMPT_COMPLETO  # placeholder real resolvido
    assert '"contexto_usuario": {' in ORQUESTRADOR_PROMPT_COMPLETO  # JSON literal preservado
    assert "session:ptr:{usuario_id}" in ORQUESTRADOR_MEMORY_TOOL
    assert '"dados_metrificados": {}' in ANALYTICS_PROMPT_COMPLETO
    # Escape duplicado vazando para o texto (`{{` só aparece se alguém escapou
    # uma string que não é f-string). `}}` é legítimo em JSON aninhado.
    for prompt in (ORQUESTRADOR_PROMPT_COMPLETO, JUIZ_ENTRADA_PROMPT_COMPLETO, ANALYTICS_PROMPT_COMPLETO):
        assert "{{" not in prompt


def test_agent_suite_builds_with_a_real_provider(monkeypatch) -> None:
    """Sem rede: só constrói os agentes (nenhuma chamada ao modelo)."""
    pytest.importorskip("langgraph")
    pytest.importorskip("langchain_ollama")
    from src.agents.factory import AgentSuite
    from src.core.config import Settings

    settings = Settings.from_env().with_overrides(
        llm_provider="ollama", ollama_base_url="http://127.0.0.1:9", tracing_provider="none"
    )
    suite = AgentSuite(settings)
    assert set(suite.agents) == {"juiz_entrada", "orquestrador", "faq", "analytics", "educacional", "grafo", "juiz_saida"}


def test_current_date_goes_in_every_real_call_not_in_the_frozen_system_prompt() -> None:
    """O prompt de sistema é montado no boot; a data dentro dele congelava."""
    import asyncio
    from datetime import datetime
    from zoneinfo import ZoneInfo

    pytest.importorskip("langgraph")
    from src.agents.factory import AgentSuite
    from src.core.config import Settings
    from src.prompts.shared.temporal import _CONTEXTO_TEMPORAL, formatar_data_hora

    suite = AgentSuite(Settings.from_env().with_overrides(llm_provider="mock", tracing_provider="none"))
    suite.settings = suite.settings.with_overrides(llm_provider="ollama")
    recebido: list[str] = []

    class FakeAgent:
        async def ainvoke(self, payload, config=None):
            recebido.append(payload["messages"][0]["content"])
            return {"messages": []}

    suite.agents["orquestrador"] = FakeAgent()
    asyncio.run(suite.invoke("orquestrador", "MENSAGEM_ORIGINAL: oi"))

    hoje = datetime.now(ZoneInfo("America/Sao_Paulo"))
    primeira, _, resto = recebido[0].partition("\n\n")
    assert primeira.startswith("DATA_HORA_ATUAL: ")
    assert f"de {hoje.year}" in primeira and resto == "MENSAGEM_ORIGINAL: oi"
    assert "DATA_HORA_ATUAL" in _CONTEXTO_TEMPORAL and str(hoje.year) not in _CONTEXTO_TEMPORAL
    assert formatar_data_hora(datetime(2026, 9, 27, 17, 5, tzinfo=ZoneInfo("America/Sao_Paulo"))).startswith(
        "domingo, 27 de setembro de 2026 — 17:05"
    )
