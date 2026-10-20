# Escopo das Tools por Usuário (IDOR) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Impedir que as tools de `analytics` e `grafo` leiam dados de outro usuário ou de outro condomínio, mesmo que o LLM peça (prompt injection ou erro do modelo).

**Architecture:** A identidade autenticada vai para uma `ContextVar` (`tool_scope`) enquanto os nós `analytics`/`grafo` rodam. Um middleware LangChain (`ToolScopeMiddleware`, via `wrap_tool_call`) intercepta TODA chamada de tool antes de executar: autoriza pela ação do `policies.py` (tabela tool → ação), sobrescreve `condominio_id` com o do usuário e `usuario_id` com o próprio usuário (exceto consulta agregada de quem tem `analytics_macro`), e nega o resto com um `ToolMessage`. As tools do grafo que recebem ids arbitrários de nós filtram no Cypher tudo que não pertence ao condomínio do usuário.

**Tech Stack:** Python 3.11, LangChain 1.3.14 (`langchain.agents.middleware.AgentMiddleware`, `ToolCallRequest.override`), Neo4j Cypher 5, pytest (modo `mock`).

**Spec:** Achado 4.1 da auditoria de 2026-10-16: "As tools de `analytics` e `grafo` não amarram a identidade. … recebem `usuario_id` e `condominio_id` como argumentos que o **LLM** escolhe … A proteção … depende só de o modelo obedecer ao prompt. … A `usuarios_mais_conectados` devolve IDs de moradores … liberada para `morador_residencial`. … injetar o `user_id` e o `condominio_id` de um `contextvar` dentro da tool e ignorar o argumento do LLM." Também o prompt `src/prompts/agents/analytics.py:23-28` já promete: "A tool já conhece `condominio_id` e `morador_id` … Nunca envie … `usuario_id`, `condominio_id` … como argumentos das tools" — hoje falso.

## Global Constraints

- **NÃO fazer `git commit`/`git add`/`git stash`/`git checkout`/`git restore`/`git push`** (regra do usuário; ele commita sozinho).
- Nenhuma dependência nova. Python 3.11.
- **Falha fechada:** sem usuário no escopo, ou tool sem entrada na tabela `TOOL_ACTIONS`, a chamada é negada.
- Reusar `authorize_action` de `src/security/policies.py`; não duplicar a tabela perfil × ação.
- Não mudar a assinatura nem o SQL das tools de analytics: o middleware só reescreve os argumentos.
- Comando de teste (Git Bash):
  `LLM_PROVIDER=mock EMBEDDING_PROVIDER=mock STORAGE_MODE=memory ENABLE_EXTERNAL_SOURCE=false "$LOCALAPPDATA/Temp/ia-eko-venv/Scripts/python" -m pytest <alvo> -q -p no:cacheprovider`
  Baseline (HEAD 5c2403e + correção do `judges.py`): 258 passed, 25 skipped.

## Regras de escopo (decisão deste plano)

| Tool | Ação exigida (`policies.py`) |
| --- | --- |
| `resumo_reciclagem_morador`, `resumo_confianca_usuario`, `taxa_aprovacao_postagens`, `listar_postagens` | `analytics_individual` |
| `consultar_minha_posicao_ranking` | `ranking_pessoal` |
| `consultar_ranking_moradores` | `ranking_moradores` |
| `consultar_ranking_torres` | `ranking_torres` |
| `material_mais_reciclado`, `resumo_reciclagem_condominio`, `comparar_torres`, `comparar_periodos`, `evolucao_reciclagem_periodo`, `desempenho_quizzes_condominio`, `ritmo_diario_torres`, `simular_projecao_reciclagem`, `simular_torre_no_ritmo_da_lider`, `usuarios_mais_conectados` | `analytics_macro` |
| `caminho_relacionamento`, `conexoes_diretas` | `grafo` |

- `condominio_id` (se a tool tiver o campo) = sempre o do usuário. Ninguém consulta outro condomínio, nem síndico.
- `usuario_id` (se a tool tiver o campo) = sempre o próprio usuário. Única exceção: quem tem `analytics_macro` (síndicos) pode deixar `usuario_id` vazio numa tool onde ele é opcional (`taxa_aprovacao_postagens`, `listar_postagens`) para obter o agregado do condomínio.
- Grafo: só nós do condomínio do usuário (o próprio `Condominio`, e `Usuario`/`Torre`/`Postagem` ligados a ele por `PERTENCE_A`/`MORA_EM`/`NO_CONDOMINIO`) ou entidades globais (`CategoriaResiduo`, `Curso`, `Cooperativa`). Caminhos inteiros e vizinhos fora disso são descartados.

## Review Focus

- Sync **e** async: os agentes rodam com `ainvoke`, então `awrap_tool_call` é o caminho real; `wrap_tool_call` precisa fazer o mesmo.
- O escopo não pode vazar entre requisições: a `ContextVar` é resetada no `finally`, e um teste confirma `current_scope() is None` depois do chat.
- Argumento ausente: o LLM pode omitir `condominio_id`/`usuario_id` (o prompt manda não enviar) — o middleware preenche.
- `usuarios_mais_conectados` (ranking de pessoas) deixa de ser acessível a `morador_residencial`.
- Cypher com `$cid` não pode ser montado por interpolação do valor: o id entra só como parâmetro.

---

### Task 1: `tool_scope` + middleware (lógica e testes unitários)

**Files:**
- Create: `src/security/tool_scope.py`
- Modify: `src/observability/metrics.py` (contador `TOOL_SCOPE_DENIED`)
- Create: `tests/test_tool_scope.py`

**Interfaces:**
- Produces (`src/security/tool_scope.py`): `tool_scope(user: UserContext | None)` (context manager); `current_scope() -> UserContext | None`; `TOOL_ACTIONS: dict[str, str]`; `scope_tool_args(user, tool_name: str, args: dict, fields: set[str]) -> tuple[dict | None, str]`; `class ToolScopeMiddleware(AgentMiddleware)`.
- Produces (`metrics.py`): `TOOL_SCOPE_DENIED` (Counter `ecociente_tool_scope_denied_total`, label `tool`).

- [ ] **Step 1: Escrever os testes (falham)** — criar `tests/test_tool_scope.py`:

```python
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
```

- [ ] **Step 2: Rodar e ver falhar** — `... -m pytest tests/test_tool_scope.py -q` → FAIL (`ModuleNotFoundError: src.security.tool_scope`).

- [ ] **Step 3: Implementar.**

3a. `src/observability/metrics.py` — adicionar antes de `def safe_label`:

```python
TOOL_SCOPE_DENIED = Counter(
    "ecociente_tool_scope_denied_total",
    "Chamadas de tool negadas pelo escopo do usuário (perfil sem a ação ou contexto ausente)",
    ["tool"],
)
```

3b. Criar `src/security/tool_scope.py`:

```python
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
        agregado_permitido = authorize_action(user, "analytics_macro").allowed
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
```

- [ ] **Step 4: Rodar e ver passar** — `... -m pytest tests/test_tool_scope.py tests/test_policies.py -q` → PASS; depois a suíte inteira → 269 passed (258 + 11), 25 skipped.

---

### Task 2: Ligar o escopo nos agentes e no Cypher do grafo

**Files:**
- Modify: `src/agents/factory.py` (`_build_langchain_agents`: `middleware=[ToolScopeMiddleware()]`)
- Modify: `src/agents/graph.py` (nós `analytics` e `grafo` dentro de `tool_scope`)
- Modify: `src/agents/graph_traversal/tools.py` (filtro de condomínio no Cypher)
- Modify: `tests/test_tool_scope.py` (testes de integração)

**Interfaces:**
- Consumes: `tool_scope`, `current_scope`, `ToolScopeMiddleware` (Task 1).
- Produces: todo agente LangChain criado pelo `AgentSuite` recebe o middleware; `analytics`/`grafo` executam com o usuário do estado no escopo; Cypher do grafo recebe o parâmetro `cid`.

- [ ] **Step 1: Escrever os testes (falham)** — acrescentar ao final de `tests/test_tool_scope.py`:

```python


def test_agent_suite_gives_every_agent_the_scope_middleware(monkeypatch):
    import pytest

    pytest.importorskip("langchain_ollama")
    import langchain.agents

    from src.agents.factory import AgentSuite
    from src.core.config import Settings

    captured: list[list] = []

    def fake_create_agent(**kwargs):
        captured.append(kwargs.get("middleware") or [])
        return object()

    monkeypatch.setattr(langchain.agents, "create_agent", fake_create_agent)
    AgentSuite(Settings.from_env().with_overrides(llm_provider="ollama", ollama_base_url="http://127.0.0.1:9", tracing_provider="none"))
    assert captured
    assert all(any(isinstance(m, ToolScopeMiddleware) for m in mws) for mws in captured)


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
    assert current_scope() is None


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
    assert "ALL(n IN nodes(caminho) WHERE" in query
    assert "$cid" in query and "{id: 7}" not in query


def test_conexoes_filters_neighbors_outside_the_condominio(monkeypatch):
    from src.agents.graph_traversal.tools import conexoes_diretas

    calls = _fake_neo4j(monkeypatch)
    with tool_scope(MORADOR):
        asyncio.run(conexoes_diretas.ainvoke({"label": "Condominio", "id": 7}))
    query, params = calls[0]
    assert params["cid"] == 7
    assert "WHERE ((vizinho:Condominio" in query


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
```

- [ ] **Step 2: Rodar e ver falhar** — `... -m pytest tests/test_tool_scope.py -q` → os 7 testes novos FALHAM.

- [ ] **Step 3: Implementar.**

3a. `src/agents/factory.py`, em `_build_langchain_agents`: adicionar o import `from src.security.tool_scope import ToolScopeMiddleware` junto aos imports locais da função e passar `middleware=[ToolScopeMiddleware()]` no `create_agent(...)`:

```python
            self.agents[name] = create_agent(
                model=self.model,
                tools=tools,
                system_prompt=system_prompt,
                name=f"ecociente_{name}",
                middleware=[ToolScopeMiddleware()],
            )
```

3b. `src/agents/graph.py`: importar `from src.security.tool_scope import tool_scope`. No nó `analytics`, trocar `answer, evidence = await self.agents.run_analytics(prompt)` por:

```python
        with tool_scope(user_context):
            answer, evidence = await self.agents.run_analytics(prompt)
```

No nó `grafo`, trocar `answer, evidence = await self.agents.invoke_with_tools("grafo", prompt)` por:

```python
        with tool_scope(user_context):
            answer, evidence = await self.agents.invoke_with_tools("grafo", prompt)
```

3c. `src/agents/graph_traversal/tools.py`:

Import: `from src.security.tool_scope import current_scope`.

Após `_RELACOES_PERMITIDAS`, adicionar:

```python
_SEM_ESCOPO = {"status": "negado", "message": "Contexto de acesso ausente para consultar o grafo."}


def _condominio_do_escopo() -> int | None:
    user = current_scope()
    return user.condominio_id if user is not None else None


def _no_escopo(var: str) -> str:
    """Predicado Cypher: o nó pertence ao condomínio `$cid` ou é entidade global do domínio."""
    # ponytail: EXISTS por nó dentro do shortestPath pode cair em busca exaustiva; ok no volume atual.
    return (
        f"(({var}:Condominio AND {var}.id = $cid) OR {var}:CategoriaResiduo OR {var}:Curso OR {var}:Cooperativa "
        f"OR EXISTS {{ ({var})-[:PERTENCE_A|MORA_EM|NO_CONDOMINIO]->(:Condominio {{id: $cid}}) }})"
    )
```

Em `caminho_relacionamento`, no início do corpo: `cid = _condominio_do_escopo()` / `if cid is None: return dict(_SEM_ESCOPO)`. Trocar a query por:

```python
    query = f"""
    MATCH (origem:{origem} {{id: $id_origem}}), (destino:{destino} {{id: $id_destino}})
    WHERE {_no_escopo("origem")} AND {_no_escopo("destino")}
    MATCH caminho = shortestPath((origem)-[{rel_pattern}*1..{profundidade_maxima}]-(destino))
    WHERE ALL(n IN nodes(caminho) WHERE {_no_escopo("n")})
    RETURN [n IN nodes(caminho) | {{label: labels(n)[0], id: n.id}}] AS nos,
           [r IN relationships(caminho) | type(r)] AS relacoes,
           length(caminho) AS distancia
    LIMIT 1
    """
```

e os parâmetros por `{"id_origem": id_origem, "id_destino": id_destino, "cid": cid}`.

Em `conexoes_diretas`, no início: mesmo `cid`/`_SEM_ESCOPO`. Query:

```python
    query = f"""
    MATCH (centro:{rotulo} {{id: $id}})
    WHERE {_no_escopo("centro")}
    MATCH (centro)-[{rel_pattern}*1..{profundidade}]-(vizinho)
    WHERE {_no_escopo("vizinho")}
    RETURN DISTINCT labels(vizinho)[0] AS label_vizinho, vizinho.id AS id_vizinho
    LIMIT 50
    """
```

parâmetros `{"id": id, "cid": cid}`.

Em `usuarios_mais_conectados`, no início: `cid = _condominio_do_escopo()` / `if cid is None or condominio_id != cid: return dict(_SEM_ESCOPO)` (o middleware já põe o `condominio_id` do usuário; isto é a defesa da própria tool).

- [ ] **Step 4: Rodar e ver passar** — `... -m pytest tests/test_tool_scope.py tests/test_judge_evidence.py tests/test_output_parsing.py tests/test_imports.py tests/test_api.py -q` → PASS; depois a suíte inteira → 276 passed (269 + 7), 25 skipped.
