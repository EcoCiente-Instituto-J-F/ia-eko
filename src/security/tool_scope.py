from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from src.observability.metrics import TOOL_SCOPE_DENIED, safe_label
from src.security.policies import authorize_action
from src.shared.context import UserContext

_scope: ContextVar[UserContext | None] = ContextVar("ecociente_tool_scope", default=None)

# Ação de policies.py exigida por tool. Tool fora daqui é negada (falha fechada).
TOOL_ACTIONS: dict[str, str] = {
    "resumo_reciclagem_morador": "analytics_individual",
    "resumo_confianca_usuario": "analytics_individual",
    "taxa_aprovacao_postagens": "analytics_individual",
    "listar_postagens": "analytics_individual",
    "consultar_minha_posicao_ranking": "ranking_pessoal",
    "consultar_ranking_moradores": "ranking_moradores",
    "consultar_ranking_torres": "ranking_torres",
    "material_mais_reciclado": "analytics_macro",
    "resumo_reciclagem_condominio": "analytics_macro",
    "comparar_torres": "analytics_macro",
    "comparar_periodos": "analytics_macro",
    "evolucao_reciclagem_periodo": "analytics_macro",
    "desempenho_quizzes_condominio": "analytics_macro",
    "ritmo_diario_torres": "analytics_macro",
    "simular_projecao_reciclagem": "analytics_macro",
    "simular_torre_no_ritmo_da_lider": "analytics_macro",
    "usuarios_mais_conectados": "analytics_macro",
    "caminho_relacionamento": "grafo",
    "conexoes_diretas": "grafo",
}

# Únicas tools em que usuario_id é opcional: vazio = agregado do condomínio (só quem tem analytics_macro).
_AGREGADO_SEM_USUARIO = {"taxa_aprovacao_postagens", "listar_postagens"}


@contextmanager
def tool_scope(user: UserContext | None) -> Iterator[None]:
    """Identidade autenticada visível às tools durante a execução de um agente."""
    token = _scope.set(user)
    try:
        yield
    finally:
        _scope.reset(token)


def current_scope() -> UserContext | None:
    return _scope.get()


def scope_tool_args(
    user: UserContext | None, tool_name: str, args: dict[str, Any], fields: set[str]
) -> tuple[dict[str, Any] | None, str]:
    """Argumentos com o escopo do usuário aplicado, ou (None, motivo) se negado.

    O LLM nunca escolhe de quem são os dados: `condominio_id` é sempre o do
    usuário e `usuario_id` é sempre o próprio — exceto o agregado do condomínio
    (usuario_id vazio) para quem tem `analytics_macro`.
    """
    if user is None:
        return None, "Contexto de acesso ausente."
    action = TOOL_ACTIONS.get(tool_name)
    if action is None:
        return None, "Ferramenta sem política de acesso."
    decision = authorize_action(user, action)
    if not decision.allowed:
        return None, decision.message
    scoped = dict(args)
    if "condominio_id" in fields:
        scoped["condominio_id"] = user.condominio_id
    if "usuario_id" in fields:
        agregado_permitido = tool_name in _AGREGADO_SEM_USUARIO and authorize_action(user, "analytics_macro").allowed
        if scoped.get("usuario_id") is not None or not agregado_permitido:
            scoped["usuario_id"] = user.user_id
    return scoped, ""


class ToolScopeMiddleware(AgentMiddleware):
    """Aplica `scope_tool_args` a toda chamada de tool, antes de ela executar."""

    def _scoped(self, request: Any) -> tuple[Any, ToolMessage | None]:
        call = request.tool_call
        fields = set((request.tool.args or {}).keys()) if request.tool is not None else set()
        args, motivo = scope_tool_args(current_scope(), call["name"], call.get("args") or {}, fields)
        if args is None:
            TOOL_SCOPE_DENIED.labels(tool=safe_label(call["name"])).inc()
            denied = ToolMessage(
                content=json.dumps({"status": "negado", "message": motivo}, ensure_ascii=False),
                tool_call_id=call["id"],
                name=call["name"],
            )
            return None, denied
        return request.override(tool_call={**call, "args": args}), None

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        scoped, denied = self._scoped(request)
        return denied if denied is not None else handler(scoped)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        scoped, denied = self._scoped(request)
        return denied if denied is not None else await handler(scoped)
