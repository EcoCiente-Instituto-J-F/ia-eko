# Juiz de Saída com Evidências — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer o juiz de saída julgar o rascunho contra o que o especialista realmente consultou (trechos do RAG ou retorno das tools), em vez de contra o próprio rascunho.

**Architecture:** O `AgentSuite` passa a devolver, junto com o texto, as saídas das tools que o agente LangChain chamou (`invoke_with_tools`). O grafo guarda isso — ou o contexto do RAG — no campo de estado `evidence`, e o `juiz_saida` envia ao LLM exatamente o contrato que o prompt já descreve: `{"rascunho_texto": ..., "especialista_json": {"rota", "evidencias", "fontes"}}`. O prompt ganha uma seção curta dizendo que `evidencias` é o único limite factual.

**Tech Stack:** Python 3.11, LangChain `create_agent` (mensagens `ToolMessage` com `type == "tool"`), LangGraph, pytest (modo `mock`).

**Spec:** Requisito do trabalho "Agente de juiz para controle de alucinações" + achado 4.2 da auditoria de 2026-10-16: "O juiz de saída não recebe o contexto recuperado pelo RAG. O prompt dele leva só os títulos das fontes e a própria resposta (`ESPECIALISTA_JSON={'resposta': candidate}`)… O prompt do juiz fala em `rascunho_texto` e `especialista_json`, mas o código envia `ESPECIALISTA_JSON=` e `PERGUNTA=`."

## Global Constraints

- **NÃO fazer `git commit`/`git push`/`git add`/`git stash`** (regra do usuário). Mudanças ficam na árvore de trabalho.
- Nenhuma dependência nova. Python 3.11.
- Não mudar o comportamento dos atalhos sem LLM: FAQ canônico (`faq_canonica`) e `coletas` continuam pulando o juiz.
- `AgentSuite.invoke(agent_name, prompt) -> str` mantém a assinatura (testes fazem monkeypatch dele).
- Evidência é truncada em `EVIDENCE_MAX_CHARS = 8000` caracteres.
- Comando de teste, com `PY` = `C:/Users/heitormiasato-imt/AppData/Local/Temp/claude/C--Users-heitormiasato-imt-Desktop-Heitor-Estudo-ia-eko/cadf3db6-9562-4860-8760-e2458be70266/scratchpad/venv/Scripts/python`:
  `LLM_PROVIDER=mock EMBEDDING_PROVIDER=mock STORAGE_MODE=memory ENABLE_EXTERNAL_SOURCE=false $PY -m pytest <alvo> -q -p no:cacheprovider`
  Baseline: 246 passed, 25 skipped.

## Review Focus

- Estado do checkpointer: a `evidence` de um turno não pode vazar para o turno seguinte — vai em `_TURN_DEFAULTS` (defensivo: hoje todo especialista que chega ao juiz já grava `evidence`, então não há teste que distinga).
- Correção (`correcao`) de `faq`/`educacional` regenera o texto com um novo `rag.search`: a evidência julgada na 2ª passada tem que ser o contexto novo.
- Especialista que não chamou tool nenhuma (`analytics` respondendo "de cabeça"): o juiz recebe `"evidencias": ""`, e o prompt manda tratar fatos específicos como não fundamentados.
- Conteúdo de `ToolMessage` que não é `str` (lista/dict) não pode quebrar a serialização.
- Evidência enorme (tool devolvendo muitas linhas) não pode inflar o prompt do juiz sem limite.

---

### Task 1: `AgentSuite` devolve as saídas das tools

**Files:**
- Modify: `src/agents/factory.py` (`invoke` → delega a `invoke_with_tools`; `run_analytics` devolve tupla; helper `tool_evidence`)
- Modify: `tests/test_output_parsing.py:202-203` (o `run_analytics` roteirizado passa a devolver tupla)
- Create: `tests/test_judge_evidence.py`

**Interfaces:**
- Produces (`src/agents/factory.py`): `EVIDENCE_MAX_CHARS = 8000`; `tool_evidence(messages: list[Any]) -> str`; `AgentSuite.invoke_with_tools(agent_name: str, prompt: str) -> tuple[str, str]` (texto, evidência); `AgentSuite.invoke(agent_name, prompt) -> str` (inalterado por fora); `AgentSuite.run_analytics(prompt) -> tuple[str, str]`.

- [ ] **Step 1: Escrever os testes (falham)** — criar `tests/test_judge_evidence.py`:

```python
from __future__ import annotations

from types import SimpleNamespace

from src.agents.factory import EVIDENCE_MAX_CHARS, tool_evidence


def test_tool_evidence_keeps_only_tool_messages():
    messages = [
        SimpleNamespace(type="human", content="quantos pontos?"),
        SimpleNamespace(type="ai", content="vou consultar"),
        SimpleNamespace(type="tool", name="resumo_reciclagem_morador", content="pontos_estimados=120"),
        SimpleNamespace(type="tool", name="ranking", content=[{"posicao": 3}]),
        SimpleNamespace(type="ai", content="Você fez 120 pontos."),
    ]
    evidence = tool_evidence(messages)
    assert "[resumo_reciclagem_morador]\npontos_estimados=120" in evidence
    assert '"posicao": 3' in evidence
    assert "vou consultar" not in evidence
    assert "quantos pontos" not in evidence


def test_tool_evidence_is_capped():
    messages = [SimpleNamespace(type="tool", name="t", content="x" * (EVIDENCE_MAX_CHARS * 2))]
    assert len(tool_evidence(messages)) == EVIDENCE_MAX_CHARS


def test_tool_evidence_empty_without_tools():
    assert tool_evidence([SimpleNamespace(type="ai", content="oi")]) == ""
```

- [ ] **Step 2: Rodar e ver falhar** — `... -m pytest tests/test_judge_evidence.py -q` → FAIL (`ImportError: cannot import name 'EVIDENCE_MAX_CHARS'`).

- [ ] **Step 3: Implementar** em `src/agents/factory.py`:

3a. Imports: adicionar `import json` junto aos imports do topo.

3b. Logo após os imports (antes de `class AgentSuite`), adicionar:

```python
# ponytail: corte simples; uma tool que devolva mais que isto precisa de um resumo próprio.
EVIDENCE_MAX_CHARS = 8000


def tool_evidence(messages: list[Any]) -> str:
    """Saídas das tools chamadas pelo agente: o que o juiz de saída usa como fato."""
    parts = []
    for message in messages:
        if getattr(message, "type", None) != "tool":
            continue
        content = message.content
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, default=str)
        parts.append(f"[{getattr(message, 'name', None) or 'tool'}]\n{content}")
    return "\n\n".join(parts)[:EVIDENCE_MAX_CHARS]
```

3c. Substituir o método `invoke` inteiro (linhas 56-86) por:

```python
    async def invoke(self, agent_name: str, prompt: str) -> str:
        text, _ = await self.invoke_with_tools(agent_name, prompt)
        return text

    async def invoke_with_tools(self, agent_name: str, prompt: str) -> tuple[str, str]:
        """Texto final do agente + saídas das tools que ele chamou (evidência)."""
        if self.settings.llm_provider == "mock":
            return self._mock_response(agent_name, prompt), ""
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
        evidence = tool_evidence(messages)
        if not messages:
            return str(result), evidence
        return self._message_text(messages[-1].content), evidence

    @staticmethod
    def _message_text(content: Any) -> str:
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
```

3d. Substituir `run_analytics` (linhas 88-94) por:

```python
    async def run_analytics(self, prompt: str) -> tuple[str, str]:
        if self.settings.llm_provider == "mock":
            return self._mock_response("analytics", prompt), ""
        started = time.perf_counter()
        result = await self.invoke_with_tools("analytics", prompt)
        TOOL_LATENCY.labels(tool="postgresql_analytics_agent").observe(time.perf_counter() - started)
        return result
```

3e. `src/agents/graph.py`, método `analytics`: trocar `answer = await self.agents.run_analytics(prompt)` por `answer, _evidence = await self.agents.run_analytics(prompt)` (a Task 2 passa a usar `_evidence`). Isso mantém a suíte verde ao fim desta task.

3f. `tests/test_output_parsing.py`, dentro de `test_e2e_json_route_is_respected`, trocar:

```python
    async def run_analytics(prompt: str) -> str:
        return await scripted("analytics", prompt)
```

por:

```python
    async def run_analytics(prompt: str) -> tuple[str, str]:
        return await scripted("analytics", prompt), ""
```

- [ ] **Step 4: Rodar e ver passar** — `... -m pytest tests/test_judge_evidence.py tests/test_output_parsing.py tests/test_imports.py tests/test_api.py -q` → PASS; depois a suíte inteira uma vez → 249 passed (246 + 3), sem falhas.

---

### Task 2: O grafo entrega as evidências ao juiz

**Files:**
- Modify: `src/agents/state.py` (campo `evidence: str`)
- Modify: `src/agents/graph.py` (`_rag_specialist`, `analytics`, `grafo`, `juiz_saida`, `correcao`, `_TURN_DEFAULTS`)
- Modify: `src/prompts/shared/judges.py` (`JUIZ_SAIDA_PROMPT`)
- Modify: `README.md` (1 frase)
- Modify: `tests/test_judge_evidence.py` (testes ponta a ponta)

**Interfaces:**
- Consumes: `AgentSuite.invoke_with_tools(...) -> tuple[str, str]`, `AgentSuite.run_analytics(prompt) -> tuple[str, str]` (Task 1).
- Produces: `EcoState["evidence"]: str`; prompt do juiz no formato `PERGUNTA=<mensagem>\nENTRADA_JSON=<json>` com `ENTRADA_JSON = {"rascunho_texto": str, "especialista_json": {"rota": str, "evidencias": str, "fontes": list[dict]}}`.

- [ ] **Step 1: Escrever os testes (falham)** — acrescentar ao final de `tests/test_judge_evidence.py`:

```python


def _recording(outputs: dict[str, str], seen: dict[str, list[str]]):
    async def invoke(agent_name: str, prompt: str) -> str:
        seen.setdefault(agent_name, []).append(prompt)
        return outputs.get(agent_name, "")

    return invoke


def _post(client, mensagem: str):
    return client.post(
        "/api/v1/chat",
        headers={"X-Usuario-Id": "77", "X-Perfil": "morador_residencial"},
        json={"mensagem": mensagem},
    )


def test_judge_receives_rag_context_as_evidence(client):
    seen: dict[str, list[str]] = {}
    client.app.state.graph.agents.invoke = _recording(
        {
            "juiz_entrada": '{"status": "aprovado"}',
            "orquestrador": '{"route": "educador"}',
            "educacional": "Separe o vidro.",
            "juiz_saida": '{"status": "aprovado"}',
        },
        seen,
    )
    assert _post(client, "Como separar resíduos recicláveis?").status_code == 200
    judge_prompt = seen["juiz_saida"][-1]
    assert '"rascunho_texto": "Separe o vidro."' in judge_prompt
    assert '"evidencias": "[Fonte: FAQ EcoCiente' in judge_prompt
    assert "ESPECIALISTA_JSON=" not in judge_prompt


def test_judge_receives_analytics_tool_output_as_evidence(client):
    seen: dict[str, list[str]] = {}
    agents = client.app.state.graph.agents
    agents.invoke = _recording(
        {
            "juiz_entrada": '{"status": "aprovado"}',
            "orquestrador": '{"route": "analytics"}',
            "juiz_saida": '{"status": "aprovado"}',
        },
        seen,
    )

    async def run_analytics(prompt: str) -> tuple[str, str]:
        return "Você fez 120 pontos.", "[resumo_reciclagem_morador]\npontos_estimados=120"

    agents.run_analytics = run_analytics
    assert _post(client, "Quantos pontos eu fiz?").status_code == 200
    assert "pontos_estimados=120" in seen["juiz_saida"][-1]


def test_judge_gets_empty_evidence_when_no_tool_was_called(client):
    seen: dict[str, list[str]] = {}
    agents = client.app.state.graph.agents
    agents.invoke = _recording(
        {
            "juiz_entrada": '{"status": "aprovado"}',
            "orquestrador": '{"route": "analytics"}',
            "juiz_saida": '{"status": "aprovado"}',
        },
        seen,
    )

    async def run_analytics(prompt: str) -> tuple[str, str]:
        return "Você fez 120 pontos.", ""

    agents.run_analytics = run_analytics
    assert _post(client, "Quantos pontos eu fiz?").status_code == 200
    assert '"evidencias": ""' in seen["juiz_saida"][-1]


def test_correction_rejudges_with_fresh_rag_evidence(client):
    seen: dict[str, list[str]] = {}
    answers = iter(['{"status": "bloqueado", "motivo_interno": "sem base"}', '{"status": "aprovado"}'])

    async def invoke(agent_name: str, prompt: str) -> str:
        seen.setdefault(agent_name, []).append(prompt)
        if agent_name == "juiz_saida":
            return next(answers)
        return {
            "juiz_entrada": '{"status": "aprovado"}',
            "orquestrador": '{"route": "educador"}',
            "educacional": "Separe o vidro.",
        }.get(agent_name, "")

    client.app.state.graph.agents.invoke = invoke
    body = _post(client, "Como separar resíduos recicláveis?").json()
    assert "correcao" in body["agents_called"]
    assert len(seen["juiz_saida"]) == 2
    assert '"evidencias": "[Fonte: FAQ EcoCiente' in seen["juiz_saida"][1]


def test_judge_prompt_defines_evidence_as_factual_limit():
    from src.prompts.shared.judges import JUIZ_SAIDA_PROMPT_COMPLETO

    assert "### PAPEL\n### PAPEL" not in JUIZ_SAIDA_PROMPT_COMPLETO
    assert "`especialista_json.evidencias`" in JUIZ_SAIDA_PROMPT_COMPLETO
```

- [ ] **Step 2: Rodar e ver falhar** — `... -m pytest tests/test_judge_evidence.py -q` → os testes ponta a ponta e o de prompt FALHAM (prompt do juiz ainda usa `ESPECIALISTA_JSON=`).

- [ ] **Step 3: Implementar.**

3a. `src/agents/state.py`: adicionar `evidence: str` logo após `sources: list[SourceResponse]`.

3b. `src/agents/graph.py`, `_rag_specialist`: no `return`, adicionar `"evidence": context,` (o `context` já existe; é `""` quando a base não tem nada).

3c. `analytics`: trocar `answer, _evidence = await self.agents.run_analytics(prompt)` por `answer, evidence = await self.agents.run_analytics(prompt)` e adicionar `"evidence": evidence,` no `return`.

3d. `grafo`: trocar `answer = await self.agents.invoke("grafo", prompt)` por `answer, evidence = await self.agents.invoke_with_tools("grafo", prompt)` e adicionar `"evidence": evidence,` no `return`.

3e. `juiz_saida`, ramo `else` (o que chama o LLM): substituir o bloco

```python
            source_info = [s.model_dump() for s in state.get("sources", [])]
            prompt = (
                f"ROTA={state.get('route')}\n"
                f"PERGUNTA={state['mensagem']}\n"
                f"FONTES={json.dumps(source_info, ensure_ascii=False)}\n"
                f"ESPECIALISTA_JSON={json.dumps({'resposta': candidate}, ensure_ascii=False)}"
            )
```

por

```python
            # Contrato do prompt: o rascunho é julgado contra o que o especialista
            # consultou (trechos do RAG ou retorno das tools), não contra si mesmo.
            entrada = {
                "rascunho_texto": candidate,
                "especialista_json": {
                    "rota": state.get("route"),
                    "evidencias": state.get("evidence", ""),
                    "fontes": [s.model_dump() for s in state.get("sources", [])],
                },
            }
            prompt = (
                f"PERGUNTA={state['mensagem']}\n"
                f"ENTRADA_JSON={json.dumps(entrada, ensure_ascii=False, default=str)}"
            )
```

3f. `correcao`: logo após `route = state.get("route", "faq")` adicionar `evidence = state.get("evidence", "")`; no ramo `if route in {"faq", "educacional"}:` adicionar `evidence = context` logo após a linha do `rag.search`; no `return`, adicionar `"evidence": evidence,`.

3g. `_TURN_DEFAULTS`: adicionar `"evidence": "",`.

3h. `src/prompts/shared/judges.py`, `JUIZ_SAIDA_PROMPT`: substituir

```
### PAPEL
### PAPEL
Você é o último filtro e o ponto de contato final com o usuário. Receba `rascunho_texto` e `especialista_json`. Revise somente o rascunho usando o JSON como limite factual. Depois de você não há nova formatação.
```

por

```
### PAPEL
Você é o último filtro e o ponto de contato final com o usuário. Receba `rascunho_texto` e `especialista_json`. Revise somente o rascunho. Depois de você não há nova formatação.

### LIMITE FACTUAL
`especialista_json.evidencias` traz o que o especialista realmente consultou: trechos recuperados da base (RAG) ou o retorno das ferramentas (PostgreSQL, Neo4j). É o único limite factual. Número, nome, data ou fato específico do rascunho que não esteja em `evidencias` é `afirmacao_nao_fundamentada`, mesmo que pareça plausível. Se `evidencias` estiver vazio, só sobrevivem orientações genéricas e a declaração de que a informação não pôde ser confirmada.
```

e, no critério 3, trocar `que não consta nem é consequência direta do \`especialista_json\`` por `que não consta nem é consequência direta de \`especialista_json.evidencias\``.

3i. `README.md`, seção "Pipeline de uma mensagem", logo abaixo do diagrama mermaid, adicionar a frase: `O juiz de saída recebe o rascunho junto com as evidências que o especialista realmente consultou — trechos recuperados pelo RAG ou o retorno das tools de PostgreSQL/Neo4j — e reprova ou censura o que não estiver nelas.`

- [ ] **Step 4: Rodar e ver passar** — `... -m pytest tests/test_judge_evidence.py tests/test_output_parsing.py tests/test_api.py tests/test_tracing_and_stream.py tests/test_faq_qdrant.py -q` → PASS; depois a suíte inteira → 254 passed (249 + 5), 25 skipped, sem falhas. Confirmar `git log -1 --format=%h` = `c0523ad`.
