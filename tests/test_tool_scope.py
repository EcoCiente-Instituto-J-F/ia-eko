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
        if args is not None:
            assert args["usuario_id"] == 5, tool


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
