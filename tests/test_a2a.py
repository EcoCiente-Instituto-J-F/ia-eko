from __future__ import annotations

import asyncio

import httpx
import pytest

from src.core.config import Settings
from src.integrations.a2a.client import ask_external_agent


def test_agent_card_is_published(client):
    r = client.get("/.well-known/agent-card.json")
    assert r.status_code == 200
    card = r.json()
    assert card["name"] == "EcoCiente Educational Agent"
    assert any(skill["id"] == "educacao_ambiental" for skill in card["skills"])


def test_a2a_client_and_server_round_trip(client):
    async def run() -> str:
        transport = httpx.ASGITransport(app=client.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as http:
            return await ask_external_agent(
                "http://127.0.0.1:8000", "Como separar resíduos recicláveis?", httpx_client=http
            )

    resposta = asyncio.run(run())
    assert resposta.startswith("Evidência EcoCiente:")
    assert "Fontes:" in resposta


def test_remote_agent_is_off_by_default():
    settings = Settings(environment="test", llm_provider="mock", embedding_provider="mock", storage_mode="memory")
    assert settings.a2a_remote_agent_url is None
    assert settings.a2a_remote_timeout_seconds == 8.0


def _remote(**overrides) -> Settings:
    return Settings.from_env().with_overrides(a2a_remote_agent_url="http://agente-externo:8000", **overrides)


def test_external_agent_tool_returns_the_answer(monkeypatch):
    from src.integrations.a2a import tool as a2a_tool

    seen: dict = {}

    async def fake(base_url, message, *, timeout, httpx_client=None):
        seen.update(url=base_url, message=message, timeout=timeout)
        return "Evidência externa: vidro é reciclável."

    monkeypatch.setattr(a2a_tool, "ask_external_agent", fake)
    consultar = a2a_tool.build_external_agent_tool(_remote(a2a_remote_timeout_seconds=5.0))
    result = asyncio.run(consultar.ainvoke({"pergunta": "Vidro é reciclável?"}))
    assert result == {"status": "ok", "agente": "http://agente-externo:8000", "resposta": "Evidência externa: vidro é reciclável."}
    assert seen == {"url": "http://agente-externo:8000", "message": "Vidro é reciclável?", "timeout": 5.0}


def test_external_agent_tool_never_sends_personal_data(monkeypatch):
    from src.integrations.a2a import tool as a2a_tool

    sent: list[str] = []

    async def fake(base_url, message, *, timeout, httpx_client=None):
        sent.append(message)
        return "ok"

    monkeypatch.setattr(a2a_tool, "ask_external_agent", fake)
    consultar = a2a_tool.build_external_agent_tool(_remote())
    asyncio.run(consultar.ainvoke({"pergunta": "Meu CPF é 123.456.789-09 e meu e-mail é ana@exemplo.com, onde descarto pilha?"}))
    assert "123.456.789-09" not in sent[0]
    assert "ana@exemplo.com" not in sent[0]


def test_external_agent_failure_is_reported_not_raised(monkeypatch):
    from src.integrations.a2a import tool as a2a_tool

    async def slow(*args, **kwargs):
        raise TimeoutError("agente externo lento")

    monkeypatch.setattr(a2a_tool, "ask_external_agent", slow)
    consultar = a2a_tool.build_external_agent_tool(_remote())
    result = asyncio.run(consultar.ainvoke({"pergunta": "Vidro é reciclável?"}))
    assert result["status"] == "indisponivel"


def test_educacional_gets_the_a2a_tool_only_when_configured(monkeypatch):
    pytest.importorskip("langchain_ollama")
    import langchain.agents

    from src.agents.factory import AgentSuite
    from src.security.tool_scope import TOOL_ACTIONS

    captured: dict[str, list[str]] = {}

    def fake_create_agent(**kwargs):
        captured[kwargs["name"]] = [t.name for t in kwargs["tools"]]
        return object()

    monkeypatch.setattr(langchain.agents, "create_agent", fake_create_agent)
    base = Settings.from_env().with_overrides(llm_provider="ollama", ollama_base_url="http://127.0.0.1:9", tracing_provider="none")

    off = AgentSuite(base.with_overrides(a2a_remote_agent_url=None))
    assert captured["ecociente_educacional"] == []
    assert off.has_tools("educacional") is False

    on = AgentSuite(base.with_overrides(a2a_remote_agent_url="http://agente-externo:8000"))
    assert captured["ecociente_educacional"] == ["consultar_agente_externo"]
    assert on.has_tools("educacional") is True
    assert TOOL_ACTIONS["consultar_agente_externo"] == "educacional"


def test_educacional_uses_the_external_agent_as_evidence_and_source(client, monkeypatch):
    from src.security.tool_scope import current_scope

    graph = client.app.state.graph
    monkeypatch.setattr(graph.settings, "a2a_remote_agent_url", "http://agente-externo:8000")
    seen: dict[str, list[str]] = {}
    scopes: list = []

    async def invoke(agent_name: str, prompt: str) -> str:
        seen.setdefault(agent_name, []).append(prompt)
        return {
            "juiz_entrada": '{"status": "aprovado"}',
            "orquestrador": '{"route": "educador"}',
            "juiz_saida": '{"status": "aprovado"}',
        }.get(agent_name, "")

    async def invoke_with_tools(agent_name: str, prompt: str) -> tuple[str, str]:
        scopes.append(current_scope())
        return "Vidro limpo vai na coleta seletiva.", '[consultar_agente_externo]\n{"status": "ok", "resposta": "Vidro é reciclável."}'

    monkeypatch.setattr(graph.agents, "invoke", invoke)
    monkeypatch.setattr(graph.agents, "invoke_with_tools", invoke_with_tools)
    monkeypatch.setattr(graph.agents, "has_tools", lambda name: name == "educacional")

    r = client.post(
        "/api/v1/chat",
        headers={"X-Usuario-Id": "77", "X-Perfil": "morador_residencial", "X-Condominio-Id": "7"},
        json={"mensagem": "Como separo vidro?"},
    )
    assert r.status_code == 200
    body = r.json()
    assert any(source["source"] == "a2a:http://agente-externo:8000" for source in body["sources"])
    assert "Vidro é reciclável." in seen["juiz_saida"][-1]
    assert scopes and scopes[0].user_id == 77


def test_card_pointing_to_another_host_is_refused(client):
    async def run():
        transport = httpx.ASGITransport(app=client.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://outro-host:9000") as http:
            return await ask_external_agent("http://outro-host:9000", "Vidro é reciclável?", httpx_client=http)

    with pytest.raises(ValueError):
        asyncio.run(run())


def test_failed_external_agent_is_not_credited_as_a_source(client, monkeypatch):
    graph = client.app.state.graph
    monkeypatch.setattr(graph.settings, "a2a_remote_agent_url", "http://agente-externo:8000")

    async def invoke(agent_name, prompt):
        return {"juiz_entrada": '{"status": "aprovado"}', "orquestrador": '{"route": "educador"}', "juiz_saida": '{"status": "aprovado"}'}.get(agent_name, "")

    async def invoke_with_tools(agent_name, prompt):
        return "Separe o vidro.", '[consultar_agente_externo]\n{"status": "indisponivel", "message": "O agente externo não respondeu."}'

    monkeypatch.setattr(graph.agents, "invoke", invoke)
    monkeypatch.setattr(graph.agents, "invoke_with_tools", invoke_with_tools)
    monkeypatch.setattr(graph.agents, "has_tools", lambda name: name == "educacional")
    r = client.post("/api/v1/chat", headers={"X-Usuario-Id": "77", "X-Perfil": "morador_residencial", "X-Condominio-Id": "7"}, json={"mensagem": "Como separo vidro?"})
    assert r.status_code == 200
    assert not any(s["source"].startswith("a2a:") for s in r.json()["sources"])


def test_judge_prompt_marks_external_agent_as_unverified():
    from src.prompts.shared.judges import JUIZ_SAIDA_PROMPT_COMPLETO

    assert "agente externo não verificado" in JUIZ_SAIDA_PROMPT_COMPLETO
