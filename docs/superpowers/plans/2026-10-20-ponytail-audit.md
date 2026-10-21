# Plano — cortes da auditoria ponytail

Spec: `docs/AUDITORIA_PONYTAIL.md`, seção 2 (itens #1–#22).
Base: `03f10e1` (working tree limpo, exceto docs novos).

## Global Constraints

- **Sem git**: nenhum `git add/commit/stash/checkout/push/reset/worktree`. O usuário commita. Só leitura (`git diff`, `git log`, `git status`).
- **Nunca matar processos** (`taskkill`, `kill`, `pkill`, `Stop-Process`). A suíte leva cerca de 4 min: esperar.
- **Comportamento observável igual**: as respostas da API, os eventos SSE, `agents_called`, as métricas usadas no Grafana e os testes existentes continuam iguais. A única exceção são as mudanças que este plano declara.
- Sem dependências novas, exceto `numpy` explícito na Task 8 (hoje já vem como transitiva do `faiss-cpu`).
- Estilo do código vizinho: comentários em português, mesma densidade, sem docstrings novas desnecessárias.
- Suíte (Git Bash): `LLM_PROVIDER=mock EMBEDDING_PROVIDER=mock STORAGE_MODE=memory ENABLE_EXTERNAL_SOURCE=false "$LOCALAPPDATA/Temp/ia-eko-venv/Scripts/python" -m pytest -q -p no:cacheprovider`. Referência: 320 passed, 25 skipped.

## Fora do plano (decisões)

- **#13** (`run_analytics`, `AgentSuite.parse_route`): 4 testes e `evals/routing/run.py` substituem esses métodos via monkeypatch. Cortar 15 linhas obrigaria a reescrever 5 arquivos de teste/eval. Fica.
- **#17** (`verificar_compactacao`): muda `agents_called`, os testes e os eventos de progresso do SSE. Fica.
- **#20** (`with_overrides`): são 37 chamadas em 28 arquivos de teste para economizar 3 linhas. Fica.

---

## Task 1: `Settings.from_env` por laço (#1, #16)

**Arquivos:** `src/core/config.py`, `.env.example`, `k8s/configmap.yaml`

1. Remover os campos `aura_instanceid`, `aura_instancename` e `run_calendar_integration_tests`, junto com a validação de `RUN_CALENDAR_INTEGRATION_TESTS` em `__post_init__`. Tirar as linhas `AURA_INSTANCEID`, `AURA_INSTANCENAME` e `RUN_CALENDAR_INTEGRATION_TESTS` de `.env.example` e de `k8s/configmap.yaml` (e o comentário imediatamente acima, se for só delas).
2. **Antes de apagar o `from_env` atual**, comparar o default de cada campo no dataclass com o default no `from_env`. Onde divergirem, o dataclass passa a usar **o valor do `from_env`**, porque é ele que vale em runtime. Caso conhecido: `neo4j_uri="bolt://localhost:7687"`, `neo4j_username="neo4j"`, `neo4j_password="neo4j"`, `neo4j_database="neo4j"` (tipo `str | None` mantido). Listar no relatório todas as divergências encontradas.
3. Substituir `_bool`, `_int`, `_float` e o corpo de `from_env` por:

```python
from dataclasses import dataclass, fields, replace

_TRUE = {"1", "true", "yes", "on", "sim"}
_PARSE = {"bool": lambda v: v.strip().lower() in _TRUE, "int": int, "float": float}
# Variáveis cujo nome não é o campo em maiúsculas.
_ENV_NAMES = {
    "environment": "APP_ENV",
    "calendar_api_url": "CALENDAR_API_BASE_URL",
    "calendar_api_timeout": "CALENDAR_API_TIMEOUT_SECONDS",
}
_LOWER = {"environment", "llm_provider", "embedding_provider", "storage_mode", "faq_backend", "tracing_provider"}

    @classmethod
    def from_env(cls) -> "Settings":
        values: dict[str, object] = {}
        for f in fields(cls):
            raw = _get(_ENV_NAMES.get(f.name, f.name.upper()))
            if raw is None:
                continue
            value = _PARSE.get(f.type, str)(raw)
            values[f.name] = value.lower() if f.name in _LOWER else value
        return cls(**values)
```

   `_get` continua igual (vazio = ausente). Com `from __future__ import annotations`, `f.type` é string (`"int"`, `"bool"`, `"float"`, `"str"`, `"str | None"`); qualquer coisa fora de `_PARSE` é tratada como `str`.
4. Conferir à mão: todo nome de env que hoje aparece em `from_env` precisa sair igual pelo laço. Gerar a lista antiga e a nova num script descartável no scratchpad e comparar. Fora `AURA_*` e `RUN_CALENDAR_INTEGRATION_TESTS`, a diferença deve ser zero.

**Teste novo** em `tests/test_config.py` (criar se não existir; se existir, acrescentar):

```python
from src.core.config import Settings


def test_from_env_reads_renamed_and_typed_vars(monkeypatch):
    monkeypatch.setenv("APP_ENV", "TEST")
    monkeypatch.setenv("CALENDAR_API_BASE_URL", "http://cal")
    monkeypatch.setenv("CALENDAR_API_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "7")
    monkeypatch.setenv("QUOTA_ENABLED", "nao")
    monkeypatch.setenv("LLM_PROVIDER", "MOCK")
    monkeypatch.setenv("QDRANT_URL", "")
    s = Settings.from_env()
    assert (s.environment, s.calendar_api_url, s.calendar_api_timeout) == ("test", "http://cal", 2.5)
    assert (s.rate_limit_per_minute, s.quota_enabled, s.llm_provider, s.qdrant_url) == (7, False, "mock", None)
```

## Task 2: código morto (#2, #8, #9, #12, #14)

**Arquivos:** os `__init__.py` listados, `src/models/`, `src/api/schemas/common.py`, `src/shared/context.py`, `src/security/roles.py`, `src/api/dependencies.py`, `src/api/main.py`, `src/services/session_service.py`, `src/services/session_lock.py`, `src/integrations/mcp/client.py`, `src/integrations/a2a/client.py`

1. Esvaziar os re-exports (deixar o arquivo vazio; se tiver docstring de pacote, manter só a docstring): `src/database/__init__.py`, `src/security/__init__.py`, `src/integrations/auth/__init__.py`, `src/integrations/calendar/__init__.py`, `src/agents/coleta/__init__.py`, `src/tools/shared/__init__.py`, `src/shared/__init__.py`. Antes, confirmar com grep que nada importa `from src.<pacote> import` desses pacotes (src, tests, evals, scripts).
2. Apagar `src/models/` inteiro (confirmar com grep `src.models`).
3. Apagar: `ServiceStatusResponse` (common.py), `UserContext.has_permission`, `ALL_PROFILES` (roles.py), `get_rag_service` e `get_graph` (dependencies.py, mais imports órfãos), `SessionService.close()` e a chamada em `main.py`, e as atribuições `app.state.calendar_api`, `app.state.calendar_mcp` e `app.state.tracing` em `main.py`. Antes de apagar cada `app.state.*`, confirmar com grep em src/tests que ninguém o lê.
4. `_storage_error_types()` (session_service) vira uma constante, e todos os usos `except _storage_error_types()` passam a `except _STORAGE_ERRORS`:

```python
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError

_STORAGE_ERRORS = (TimeoutError, OSError, PyMongoError, RedisError)
```

   Em `session_lock.py`, `_redis_errors()` vira `_REDIS_ERRORS = (OSError, TimeoutError, ConnectionError, RedisError)`, com o import no topo. Mesma troca nos usos.
5. Apagar `consultar_via_mcp`, `_main` e o bloco `__main__` de `src/integrations/mcp/client.py`, e também `_main` e o `__main__` de `src/integrations/a2a/client.py`. Antes, conferir em `docs/` e `README*.md` se algum comando `python -m src.integrations...client` está documentado. Se estiver, remover a linha do doc também e listar no relatório.

**Teste:** a suíte inteira (o `tests/test_imports.py` cobre os imports quebrados).

## Task 3: `llm.py` com `init_chat_model`/`init_embeddings` (#3)

**Arquivo:** `src/core/llm.py`

Substituir `build_chat_model` por:

```python
from langchain.chat_models import init_chat_model

# provedor → (model_provider do LangChain, campo do modelo, campo da chave)
_CHAT_PROVIDERS = {
    "ollama": ("ollama", "ollama_model", None),
    "groq": ("groq", "groq_model", "groq_api_key"),
    "gemini": ("google_genai", "gemini_model", "gemini_api_key"),
    "deepseek": ("deepseek", "deepseek_model", "deepseek_api_key"),
}


def build_chat_model(settings: Settings) -> Any | None:
    """Cria o chat model. `mock` não cria modelo e nunca chama API externa."""
    provider = settings.llm_provider.lower()
    if provider == "mock":
        return None
    if provider not in _CHAT_PROVIDERS:
        raise ProviderConfigurationError(
            f"LLM_PROVIDER={provider!r} inválido. Use ollama, mock, groq, gemini ou deepseek."
        )
    model_provider, model_field, key_field = _CHAT_PROVIDERS[provider]
    kwargs: dict[str, Any] = {"temperature": 0.1}
    if key_field is None:
        kwargs["base_url"] = settings.ollama_base_url
    else:
        if not getattr(settings, key_field):
            raise ProviderConfigurationError(f"{key_field.upper()} é obrigatória quando LLM_PROVIDER={provider}.")
        kwargs["api_key"] = getattr(settings, key_field)
    try:
        return init_chat_model(getattr(settings, model_field), model_provider=model_provider, **kwargs)
    except ImportError as exc:
        raise ProviderConfigurationError(f"Instale requirements-optional.txt para usar {provider}.") from exc
```

`build_embeddings` segue o mesmo padrão com `from langchain.embeddings import init_embeddings`, mantendo o ramo `mock` → `HashEmbeddings` e `"local"` como alias de `ollama`.

**Verificar e relatar** (com `inspect` no venv, sem rede): `ChatGoogleGenerativeAI`, `ChatGroq` e `ChatDeepSeek` aceitam `api_key=`. Se algum não aceitar, usar o nome certo só para ele (ex.: `google_api_key`) num dict de override. `init_embeddings("ollama:<modelo>", base_url=...)` também precisa funcionar. Se `init_embeddings` não suportar algum dos provedores, deixar `build_embeddings` como está e relatar.

**Testes:** `tests/test_imports.py` (deepseek sem chave levanta `ProviderConfigurationError`; ollama monta `AgentSuite`). Acrescentar:

```python
def test_unknown_and_keyless_providers_fail_clearly():
    from src.core.config import Settings
    from src.core.llm import ProviderConfigurationError, build_chat_model

    base = Settings.from_env()
    for overrides in ({"llm_provider": "xpto"}, {"llm_provider": "groq", "groq_api_key": None}):
        with pytest.raises(ProviderConfigurationError):
            build_chat_model(base.with_overrides(**overrides))
    assert type(build_chat_model(base.with_overrides(llm_provider="ollama"))).__name__ == "ChatOllama"
```

## Task 4: MCP do calendário e Coletas (#4, #11, #19)

**Arquivos:** `src/integrations/mcp/server.py`, `src/agents/coleta/agent.py`, `src/agents/coleta/tools.py`, `tests/test_external_integrations.py`

1. `server.py`: apagar `_build_filters` e chamar `CalendarFilters(...)` direto. Trocar as duas tuplas de 6 exceções por `except CalendarApiError` (importar a base de `src.integrations.calendar.exceptions`). Juntar a abertura repetida num helper:

```python
def _authorized_call() -> tuple[CalendarToolRuntime, str] | CalendarToolError:
    """Runtime + token, ou o erro que a tool devolve sem chamar a API."""
    try:
        runtime = _runtime()
    except RuntimeError:
        return CalendarToolError(code="authentication_required", message="Contexto autenticado do calendário ausente.")
    if runtime.calendar_api is None:
        return CalendarToolError(code="not_configured", message="Integração de calendário não configurada.")
    token = _bearer_token(runtime)
    if token is None:
        return CalendarToolError(code="authentication_required", message="Bearer token do calendário indisponível.")
    return runtime, token
```

   Em cada tool: `auth = _authorized_call()`; `if isinstance(auth, CalendarToolError): return <Result>(ok=False, error=auth)`; `runtime, token = auth`. As mensagens e os códigos de erro ficam idênticos.
2. Apagar `src/agents/coleta/tools.py`. Em `CollectionAgent`, `self.tools = CalendarTools(mcp_client)` vira `self.calendar = mcp_client`. As chamadas passam a ser `self.calendar.buscar_proxima(user, **filters)` e `self.calendar.listar_agendamentos(user, **filters)`.
3. `tests/test_external_integrations.py`: `FakeCalendarTools` passa a se chamar `FakeCalendarMcp`, com os métodos `buscar_proxima` (e `listar_agendamentos`, se existir equivalente) no lugar de `next_collection`/`list_collections`. As atribuições `collection_agent.tools = ...` viram `collection_agent.calendar = ...`. Os asserts não mudam.
4. `_message_date`:

```python
    @staticmethod
    def _message_date(message: str) -> date | None:
        for pattern, fmt in ((r"\b\d{2}/\d{2}/\d{4}\b", "%d/%m/%Y"), (r"\b\d{4}-\d{2}-\d{2}\b", "%Y-%m-%d")):
            match = re.search(pattern, message)
            if match:
                try:
                    return datetime.strptime(match.group(0), fmt).date()
                except ValueError:
                    return None
        return None
```

**Testes:** `tests/test_external_integrations.py` e os testes de coletas/MCP existentes. Acrescentar um teste de `_message_date` com `"31/12/2026"`, `"2026-12-31"` (ambos → `date(2026,12,31)`), `"31/02/2026"` → `None` e `"sem data"` → `None`.

## Task 5: nós repetidos do grafo e texto da mensagem (#5, #10 parte 1)

**Arquivos:** `src/agents/graph.py`, `src/agents/factory.py`

1. `analytics` e `grafo` passam a delegar para:

```python
    async def _tool_specialist(self, state: EcoState, name: str, instruction: str, invoke) -> dict[str, Any]:
        started = time.perf_counter()
        user_context = state.get("user_context")
        safe_user_context = user_context.for_agent() if user_context else {}
        prompt = (
            f"PERGUNTA_ORIGINAL={state['mensagem']}\n"
            f"USUARIO_ID={state['usuario_id']}\n"
            f"PERFIL={state.get('perfil', 'nao_informado')}\n"
            f"CONDOMINIO_ID={state.get('condominio_id')}\n"
            f"CONTEXTO_IDENTIDADE={json.dumps(safe_user_context, ensure_ascii=False, default=str)}\n"
            f"CONTEXTO_MEMORIA=\n{self._format_memory_context(state.get('memory_context', {}))}\n"
            f"{instruction}"
        )
        with tool_scope(user_context):
            answer, evidence = await invoke(prompt)
        return {
            **self._mark(state, name, started),
            "candidate_answer": answer,
            "sources": [],
            "evidence": evidence,
            "agent": name,
        }

    async def analytics(self, state: EcoState) -> dict[str, Any]:
        return await self._tool_specialist(
            state, "analytics",
            "Use exclusivamente ferramentas autorizadas e não invente números quando a consulta falhar.",
            self.agents.run_analytics,
        )

    async def grafo(self, state: EcoState) -> dict[str, Any]:
        return await self._tool_specialist(
            state, "grafo",
            "Use exclusivamente ferramentas autorizadas e nunca invente uma conexão que a tool não confirmou.",
            lambda prompt: self.agents.invoke_with_tools("grafo", prompt),
        )
```

   `self.agents.run_analytics` precisa ser lido **na hora da chamada** (os testes substituem `agents.run_analytics` depois da montagem). Por isso a referência fica dentro do método `analytics`, e não num dict criado no `__init__`.
2. Em `juiz_saida`, os três dicts de bypass passam a usar:

```python
    @staticmethod
    def _bypass(candidate: str, motivo: str) -> dict[str, Any]:
        ok = bool(candidate.strip())
        return {
            "aprovado": ok,
            "motivo": motivo,
            "categoria": "aprovado" if ok else "resposta_vazia",
            "necessita_correcao": False,
            "resposta_censurada": None,
        }
```

   O caso Qdrant vira `_bypass(candidate, "resposta_canonica_qdrant")` e o de coletas vira `_bypass(candidate, "resposta_verificada_por_calendario")`. O caso `resposta_vazia` (com `necessita_correcao=True`) **continua literal**, porque é diferente. Os comentários explicativos de cada `elif` ficam.
3. `factory.py`: apagar `_message_text`; a linha final de `invoke_with_tools` vira `return messages[-1].text, evidence`. Se algum teste montar mensagens fake sem `.text` (procurar `FakeAgent`, `messages` com `SimpleNamespace`/dict), usar `AIMessage` no fake e relatar.

**Testes:** `tests/test_judge_evidence.py`, `tests/test_tool_scope.py`, `tests/test_output_parsing.py`, `tests/test_imports.py`, `tests/test_api.py`.

## Task 6: núcleo comum de `chat()`/`stream()` (#7, #8 parte)

**Arquivos:** `src/services/chat_service.py`, `src/api/main.py`

1. Remover o parâmetro e o atributo `tracing` de `ChatService` e o `tracing=tracing` em `main.py` (procurar `ChatService(` em tests e ajustar as chamadas que passarem `tracing=`).
2. Extrair o miolo repetido:

```python
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass, field


@dataclass
class _Turn:
    trace: ConversationTrace | None = None
    state: dict[str, Any] = field(default_factory=dict)
    answer: str = ""

    @asynccontextmanager
    async def _turn(self, request: ChatRequest, request_id: str, user_context: UserContext, session_id: str):
        """Lock da sessão → teto → mensagem do usuário → contexto de trace/token.
        Ao sair sem erro: marca bloqueio e grava a resposta (`turn.answer`)."""
        user_id = user_context.user_id
        async with self.sessions.conversation_guard(session_id):
            await self._recheck_session(user_context, session_id)
            message_id = await self.sessions.append_message(session_id, user_id, "user", request.mensagem)
            turn = _Turn()
            with conversation_trace(
                session_id=session_id,
                user_id=user_id,
                request_id=request_id,
                perfil=user_context.perfil,
            ) as trace, authenticated_request_context(user_context.token):
                turn.trace = trace
                yield turn
            if turn.state.get("blocked"):
                await self.sessions.flag_message_blocked(session_id, user_id, message_id)
            turn.answer = await self._finish(session_id, user_id, turn.state)
```

   `chat()`: `async with self._turn(...) as turn: turn.state = await self.graph.invoke(self._initial_state(...))`, com o mesmo `try/except Exception: refund; raise` em volta. `stream()`: o mesmo `async with`, com o laço `aclosing(self.graph.astream(...))` dentro, atribuindo `turn.state = chunk` nos `values`. Os três `except` do stream ficam como estão. Depois, `trace=turn.trace` e `answer=turn.answer` vão para `_response`.
   `self.sessions.conversation_guard` e `self.graph.invoke` continuam sendo chamados pelo atributo (os testes de limites e de lock os substituem).
3. Comentários existentes (ordem da sessão, `aclosing`, refund) ficam junto do código que explicam.

**Testes:** `tests/test_limits.py`, `tests/test_session_lock.py`, `tests/test_tracing_and_stream.py`, `tests/test_long_term_memory.py`, `tests/test_api.py`.

## Task 7: remoção de acentos e texto do resumidor (#15, #10 parte 2)

**Arquivos:** `src/shared/text.py` (novo), `src/security/roles.py`, `src/security/policies.py`, `src/agents/coleta/agent.py`, `src/agents/analytics/tools/common.py`, `src/etl/populate_qdrant.py`, `src/services/memory_summarizer_service.py`

1. Criar `src/shared/text.py`:

```python
import unicodedata


def fold(value: str) -> str:
    """Minúsculas, sem acentos e com espaços normalizados."""
    value = unicodedata.normalize("NFD", value.lower())
    return " ".join("".join(ch for ch in value if unicodedata.category(ch) != "Mn").split())
```

2. Substituir as cópias, preservando cada contrato:
   - `policies._normalized_text` e `CollectionAgent._normalize` → `fold` (idênticos; apagar as funções e trocar as chamadas).
   - `common.normalize_text(value)` → `fold(value) if value else ""`. Manter o nome `normalize_text`, porque é usado pelas tools; o corpo vira essa linha.
   - `roles._key` usa **maiúsculas** e troca `-`/espaços por `_`. O novo corpo é `"_".join(fold(value.replace("-", " ")).upper().split())`. Conferir que dá o mesmo resultado nos aliases de `PROFILE_ALIASES`.
   - `populate_qdrant._sem_acento`: ler o contrato atual (pode não baixar a caixa nem normalizar espaços). Só trocar se `fold` preservar o resultado em todos os usos; senão, deixar e relatar.
3. `memory_summarizer_service.py`: apagar `_content_text`; o retorno vira `return (result.text if hasattr(result, "text") else str(result)).strip()`. Se os testes passarem `str` ou um fake como `result`, isso cobre. Verificar.

**Teste novo** (`tests/test_text.py`):

```python
from src.security.roles import normalize_profile
from src.shared.text import fold


def test_fold_and_profile_aliases():
    assert fold("  Síndico   Residêncial ") == "sindico residencial"
    assert normalize_profile("síndico-residencial") == "SINDICO_RESIDENCIAL"
    assert normalize_profile("Usuário comum") == "USUARIO_COMUM"
```

## Task 8: índice vetorial nativo e HTML sem bs4 (#18, #21, #22)

**Arquivos:** `src/services/rag_service.py`, `requirements.txt`, `README.md`, `README-en.md`, `src/core/config.py` (só o comentário do `faq_backend`)

1. `_to_thread` (staticmethod) é apagado; as chamadas viram `asyncio.to_thread(func, *args, **kwargs)`, com `import asyncio` no topo.
2. `from langchain_community.vectorstores import FAISS` → `from langchain_core.vectorstores import InMemoryVectorStore`; `FAISS.from_documents` → `InMemoryVectorStore.from_documents`. `similarity_search(query, k=...)` tem a mesma assinatura.
3. Trocar o BeautifulSoup em `_fetch_external` por `html.parser`:

```python
from html.parser import HTMLParser


class _TextExtractor(HTMLParser):
    """Texto visível da página, sem script/style/noscript/svg."""

    _SKIP = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._depth:
            self._depth -= 1

    def handle_data(self, data):
        if not self._depth:
            self.parts.append(data)
```

   Uso: `parser = _TextExtractor(); parser.feed(response.text)`; depois `text = "\n".join(line.strip() for line in "\n".join(parser.parts).splitlines() if line.strip())`. O limite de 120 000 caracteres fica.
4. `requirements.txt`: remover `faiss-cpu`, `langchain-community` e `beautifulsoup4`; adicionar `numpy>=1.26,<3` sob "IA / agentes / grafo", com o comentário `# InMemoryVectorStore (similaridade por cosseno)`. Confirmar com grep que `langchain_community` e `bs4` não são usados em mais nenhum lugar (src, tests, evals, scripts, Dockerfile).
5. READMEs: "RAG (FAISS + ..." → "RAG (busca vetorial em memória + ...". O comentário em `config.py` sobre `"rag"` = "fluxo antigo (FAISS + LLM)" vira "(RAG em memória + LLM)".

**Teste novo** em `tests/test_rag_text.py`:

```python
from src.services.rag_service import _TextExtractor


def test_text_extractor_skips_scripts():
    parser = _TextExtractor()
    parser.feed("<html><style>x{}</style><p>Olá</p><script>alert(1)</script><svg><text>n</text></svg><p>mundo</p></html>")
    assert "".join(parser.parts) == "Olámundo"
```

   Mais a suíte inteira (`tests/test_api.py` indexa o RAG com `HashEmbeddings`).

## Task 9: pr-bot (#6)

**Arquivo:** `scripts/pr-bot/generate_pr.py`

```python
def github(method, path, **kwargs):
    response = requests.request(
        method,
        f"{GITHUB_API_URL}{path}",
        headers=github_headers(),
        timeout=30,
        **kwargs,
    )
    response.raise_for_status()
    return response.json()


def github_paginate(path):
    items = []
    page = 1
    while True:
        batch = github("GET", path, params={"per_page": 100, "page": page})
        items.extend(batch)
        if len(batch) < 100:
            return items
        page += 1
```

`github_get(path, params)` → `github("GET", path, params=params)`; `github_patch(path, payload)` → `github("PATCH", path, json=payload)`. `get_commits()`/`get_files()` → `github_paginate(f"/repos/{GITHUB_REPOSITORY}/pulls/{PR_NUMBER}/commits")` e `.../files`. Mantê-las como funções de uma linha, ou fazer o inline em `main()`. Não reformatar o resto do arquivo.

**Teste:** `python -m py_compile scripts/pr-bot/generate_pr.py` e um teste rápido em script descartável no scratchpad, com `requests.request` substituído via `unittest.mock`, validando a paginação (100 + 3 itens → 103, duas chamadas). O teste descartável não entra no repo.

---

## Ordem e arquivos compartilhados

T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9. Sobreposições: `main.py` (T2 e T6, em linhas diferentes), `coleta/agent.py` (T4 e T7) e `config.py` (T1 e T8, só um comentário). Cada task trabalha sobre o resultado da anterior.
