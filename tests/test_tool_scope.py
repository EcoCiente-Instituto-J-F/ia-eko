from __future__ import annotations

import asyncio

from langchain_core.messages import ToolMessage

from src.security.roles import MORADOR_RESIDENCIAL, SINDICO_RESIDENCIAL, USUARIO_COMUM
from src.security.tool_scope import (
    TOOL_ACTIONS,
    ToolScopeMiddleware,
    current_scope,
    scope_tool_args,
    tool_scope,
)
from src.shared.context import UserContext

MORADOR = UserContext(user_id=77, perfil=MORADOR_RESIDENCIAL, condominio_id=7)
SINDICO = UserContext(user_id=5, perfil=SINDICO_RESIDENCIAL, condominio_id=7)
COMUM = UserContext(user_id=9, perfil=USUARIO_COMUM, condominio_id=None)


def test_every_analytics_and_grafo_tool_has_a_policy():
    from src.agents.analytics.tools import TOOLS as ANALYTICS_TOOLS
    from src.agents.graph_traversal.tools import TOOLS as GRAFO_TOOLS

    names = {t.name for t in [*ANALYTICS_TOOLS, *GRAFO_TOOLS]}
    assert names <= set(TOOL_ACTIONS), names - set(TOOL_ACTIONS)


def test_condominio_is_always_the_users():
    args, motivo = scope_tool_args(SINDICO, "resumo_reciclagem_condominio", {"condominio_id": 999}, {"condominio_id", "periodo"})
    assert motivo == ""
    assert args["condominio_id"] == 7


def test_missing_ids_are_filled_from_the_scope():
    args, _ = scope_tool_args(MORADOR, "resumo_reciclagem_morador", {}, {"usuario_id", "periodo"})
    assert args["usuario_id"] == 77


def test_morador_cannot_read_another_users_data():
    args, _ = scope_tool_args(MORADOR, "resumo_reciclagem_morador", {"usuario_id": 999}, {"usuario_id"})
    assert args["usuario_id"] == 77


def test_morador_optional_usuario_is_forced_to_self():
    args, _ = scope_tool_args(MORADOR, "taxa_aprovacao_postagens", {"usuario_id": None}, {"usuario_id", "condominio_id"})
    assert args == {"usuario_id": 77, "condominio_id": 7}


def test_sindico_may_ask_the_condominio_aggregate_but_not_another_user():
    agregado, _ = scope_tool_args(SINDICO, "taxa_aprovacao_postagens", {}, {"usuario_id", "condominio_id"})
    assert agregado.get("usuario_id") is None and agregado["condominio_id"] == 7
    outro, _ = scope_tool_args(SINDICO, "listar_postagens", {"usuario_id": 999}, {"usuario_id", "condominio_id"})
    assert outro["usuario_id"] == 5


def test_sindico_required_usuario_is_filled_with_self():
    for tool in ("resumo_reciclagem_morador", "resumo_confianca_usuario", "consultar_minha_posicao_ranking"):
        args, _ = scope_tool_args(SINDICO, tool, {}, {"usuario_id", "condominio_id"})
        assert args is not None and args["usuario_id"] == 5, tool


def test_morador_is_denied_macro_and_people_ranking_by_connections():
    for tool in ("resumo_reciclagem_condominio", "usuarios_mais_conectados"):
        args, motivo = scope_tool_args(MORADOR, tool, {"condominio_id": 7}, {"condominio_id"})
        assert args is None and motivo


def test_fail_closed_without_user_or_policy():
    assert scope_tool_args(None, "resumo_reciclagem_morador", {}, {"usuario_id"})[0] is None
    assert scope_tool_args(MORADOR, "tool_desconhecida", {}, set())[0] is None
    assert scope_tool_args(COMUM, "resumo_reciclagem_morador", {}, {"usuario_id"})[0] is None


def test_scope_contextvar_is_reset():
    with tool_scope(MORADOR):
        assert current_scope() is MORADOR
    assert current_scope() is None


def _request(name: str, args: dict):
    from langchain.agents.middleware.types import ToolCallRequest

    from src.agents.analytics.tools import TOOLS
    from src.agents.graph_traversal.tools import TOOLS as GRAFO

    tool = next(t for t in [*TOOLS, *GRAFO] if t.name == name)
    return ToolCallRequest(tool_call={"name": name, "args": args, "id": "call-1"}, tool=tool, state={}, runtime=None)


def test_middleware_rewrites_args_before_the_tool_runs():
    seen: list[dict] = []

    async def handler(request):
        seen.append(request.tool_call["args"])
        return ToolMessage(content="ok", tool_call_id=request.tool_call["id"])

    with tool_scope(MORADOR):
        result = asyncio.run(
            ToolScopeMiddleware().awrap_tool_call(_request("resumo_reciclagem_morador", {"usuario_id": 999}), handler)
        )
    assert result.content == "ok"
    assert seen == [{"usuario_id": 77}]


def test_middleware_denies_without_calling_the_tool():
    called: list[bool] = []

    def handler(request):
        called.append(True)
        return ToolMessage(content="ok", tool_call_id="call-1")

    with tool_scope(MORADOR):
        result = ToolScopeMiddleware().wrap_tool_call(_request("usuarios_mais_conectados", {"condominio_id": 7}), handler)
    assert called == []
    assert isinstance(result, ToolMessage)
    assert '"status": "negado"' in result.content


def test_agents_with_tools_get_the_scope_middleware(monkeypatch):
    import pytest

    pytest.importorskip("langchain_ollama")
    import langchain.agents

    from src.agents.factory import AgentSuite
    from src.core.config import Settings

    captured: dict[str, list] = {}

    def fake_create_agent(**kwargs):
        captured[kwargs["name"]] = kwargs.get("middleware") or []
        return object()

    monkeypatch.setattr(langchain.agents, "create_agent", fake_create_agent)
    AgentSuite(Settings.from_env().with_overrides(llm_provider="ollama", ollama_base_url="http://127.0.0.1:9", tracing_provider="none"))
    for name in ("ecociente_analytics", "ecociente_grafo"):
        assert any(isinstance(m, ToolScopeMiddleware) for m in captured[name]), name


def _headers(user_id: int = 77, perfil: str = "morador_residencial") -> dict[str, str]:
    return {"X-Usuario-Id": str(user_id), "X-Perfil": perfil, "X-Condominio-Id": "7"}


def _script_route(client, route: str) -> None:
    async def invoke(agent_name: str, prompt: str) -> str:
        return {
            "juiz_entrada": '{"status": "aprovado"}',
            "orquestrador": '{"route": "%s"}' % route,
            "juiz_saida": '{"status": "aprovado"}',
        }.get(agent_name, "")

    client.app.state.graph.agents.invoke = invoke


def test_analytics_node_runs_with_the_authenticated_user_in_scope(client):
    seen: list = []
    _script_route(client, "analytics")

    async def run_analytics(prompt: str) -> tuple[str, str]:
        seen.append(current_scope())
        return "ok", ""

    client.app.state.graph.agents.run_analytics = run_analytics
    r = client.post("/api/v1/chat", headers=_headers(), json={"mensagem": "Quantos pontos eu fiz?"})
    assert r.status_code == 200
    assert seen and seen[0].user_id == 77 and seen[0].condominio_id == 7


def test_grafo_node_runs_with_the_authenticated_user_in_scope(client):
    seen: list = []
    _script_route(client, "grafo")

    async def invoke_with_tools(agent_name: str, prompt: str) -> tuple[str, str]:
        seen.append(current_scope())
        return "ok", ""

    client.app.state.graph.agents.invoke_with_tools = invoke_with_tools
    r = client.post("/api/v1/chat", headers=_headers(), json={"mensagem": "Qual a conexão entre mim e o síndico?"})
    assert r.status_code == 200
    assert seen and seen[0].user_id == 77 and seen[0].condominio_id == 7


def _fake_neo4j(monkeypatch) -> list[tuple[str, dict]]:
    from src.database.neo4j import neo4j_db

    calls: list[tuple[str, dict]] = []

    async def execute(query, parameters=None):
        calls.append((query, parameters or {}))
        return []

    monkeypatch.setattr(neo4j_db, "execute", execute)
    return calls


def test_caminho_is_restricted_to_the_users_condominio(monkeypatch):
    from src.agents.graph_traversal.tools import caminho_relacionamento

    calls = _fake_neo4j(monkeypatch)
    with tool_scope(MORADOR):
        asyncio.run(caminho_relacionamento.ainvoke(
            {"label_origem": "Usuario", "id_origem": 77, "label_destino": "Usuario", "id_destino": 5}
        ))
    query, params = calls[0]
    assert params["cid"] == 7
    assert "(NOT n:Usuario OR n.id = $uid)" in query
    assert "ALL(n IN nodes(caminho) WHERE" in query
    assert "$cid" in query and "{id: 7}" not in query


def test_conexoes_filters_neighbors_outside_the_condominio(monkeypatch):
    from src.agents.graph_traversal.tools import conexoes_diretas

    calls = _fake_neo4j(monkeypatch)
    with tool_scope(MORADOR):
        asyncio.run(conexoes_diretas.ainvoke({"label": "Condominio", "id": 7}))
    query, params = calls[0]
    assert params["cid"] == 7
    assert "ALL(x IN nodes(p) WHERE" in query
    assert "x:Condominio AND x.id = $cid" in query
    assert "(NOT x:Usuario OR x.id = $uid)" in query
    assert params["uid"] == 77


def test_grafo_tools_refuse_without_scope(monkeypatch):
    from src.agents.graph_traversal.tools import caminho_relacionamento, conexoes_diretas, usuarios_mais_conectados

    calls = _fake_neo4j(monkeypatch)
    results = [
        asyncio.run(caminho_relacionamento.ainvoke({"label_origem": "Usuario", "id_origem": 1, "label_destino": "Usuario", "id_destino": 2})),
        asyncio.run(conexoes_diretas.ainvoke({"label": "Usuario", "id": 1})),
        asyncio.run(usuarios_mais_conectados.ainvoke({"condominio_id": 7})),
    ]
    assert calls == []
    assert all(r["status"] == "negado" for r in results)


def test_usuarios_mais_conectados_refuses_another_condominio(monkeypatch):
    from src.agents.graph_traversal.tools import usuarios_mais_conectados

    calls = _fake_neo4j(monkeypatch)
    with tool_scope(SINDICO):
        result = asyncio.run(usuarios_mais_conectados.ainvoke({"condominio_id": 999}))
    assert result["status"] == "negado"
    assert calls == []


def test_user_without_condominio_is_denied_condominio_tools():
    sem_condo = UserContext(user_id=5, perfil=SINDICO_RESIDENCIAL, condominio_id=None)
    for tool in ("listar_postagens", "material_mais_reciclado", "taxa_aprovacao_postagens"):
        args, motivo = scope_tool_args(sem_condo, tool, {}, {"usuario_id", "condominio_id"})
        assert args is None and motivo, tool


def test_sindico_listar_postagens_is_always_own():
    args, _ = scope_tool_args(SINDICO, "listar_postagens", {}, {"usuario_id", "condominio_id"})
    assert args["usuario_id"] == 5


def test_sindico_graph_scope_does_not_restrict_to_self(monkeypatch):
    from src.agents.graph_traversal.tools import conexoes_diretas

    calls = _fake_neo4j(monkeypatch)
    with tool_scope(SINDICO):
        asyncio.run(conexoes_diretas.ainvoke({"label": "Condominio", "id": 7}))
    query, params = calls[0]
    assert "NOT x:Usuario" not in query
    assert params["cid"] == 7


def test_ranking_routes_refuse_tokens_without_condominio(client):
    r = client.get("/api/v1/rankings/moradores", params={"ciclo_id": 1, "condominio_id": 7},
                   headers={"X-Usuario-Id": "5", "X-Perfil": "sindico_residencial"})
    assert r.status_code == 403
