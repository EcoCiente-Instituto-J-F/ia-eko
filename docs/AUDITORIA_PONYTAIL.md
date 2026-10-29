# Auditoria do ia-eko — estrutura, excesso de código e possíveis erros

Data: 2026-10-20 · Branch: `feat/roi-harder-guardrail` (base `03f10e1`)

Este documento reúne três coisas:

1. **Como o sistema funciona** — estrutura de pastas, fluxo de uma mensagem, onde cada responsabilidade mora.
2. **Pontos de melhoria (ponytail)** — o que dá para apagar, simplificar ou trocar por biblioteca padrão/nativa.
3. **Possíveis erros** — falhas de lógica, segurança ou robustez, com arquivo, linha e em que situação aparecem.

Nada foi alterado no código. Linhas citadas referem-se ao commit `03f10e1`.

---

## 1. Estrutura e lógica

### 1.1 Pastas

| Pasta | O que tem |
|---|---|
| `src/api/` | FastAPI: `main.py` (lifespan monta todos os serviços), `dependencies.py` (identidade e getters de `app.state`), `routes/` e `schemas/` |
| `src/agents/` | O grafo LangGraph (`graph.py`), a fábrica de agentes LangChain + respostas mock (`factory.py`), parsing das saídas dos LLMs (`output_parsing.py`) e os especialistas com tools (`analytics/`, `graph_traversal/`, `coleta/`) |
| `src/prompts/` | Prompts de sistema por agente (`agents/`) e compartilhados (`shared/`: persona, roteador, orquestrador, juízes, data/hora) |
| `src/services/` | Regras de aplicação: chat, sessão/memória, lock de sessão, cotas, RAG (FAISS), FAQ no Qdrant, ranking (Redis), health, resumidor de memória, embeddings |
| `src/security/` | Autenticação (API externa), perfis, políticas por rota/ação, guardrails regex, escopo das tools (`tool_scope`), token da request em `ContextVar` |
| `src/integrations/` | Clientes externos: API de auth, API de calendário (HTTP), servidor/cliente MCP do calendário, A2A (servidor e cliente de agente externo) |
| `src/database/` | Singletons de conexão: Mongo (async), Redis (async), Postgres (pool psycopg2, síncrono), Neo4j (async) |
| `src/observability/` | Métricas Prometheus, tracing Langfuse/LangSmith com máscara de PII, custo/ROI, middleware de request-id |
| `src/etl/` | Jobs batch: Postgres → Neo4j (`populate_graph.py`, `validate_graph.py`) e FAQ canônico → Qdrant (`populate_qdrant.py`) |
| `src/core/` | `config.py` (todas as variáveis de ambiente), `llm.py` (escolha de provedor), `logging.py` (JSON) |
| `evals/routing/` | Avaliação do roteamento do orquestrador contra `dataset.jsonl` |
| `scripts/` | `pr-bot` (descrição de PR via Gemini, roda no GitHub Actions) e benchmark da API |
| `k8s/`, `docker-compose*.yml`, `observability/` | Deploy, CronJobs do ETL, Prometheus e Grafana |

### 1.2 Endpoints

| Método | Rota | Arquivo |
|---|---|---|
| GET | `/`, `/health`, `/metrics` | `routes/root.py`, `routes/health.py`, `routes/observability.py` |
| POST | `/api/v1/chat` | `routes/chat.py` |
| POST | `/api/v1/chat/stream` (SSE: `session`, `progress`, `answer`, `done`, `error`) | `routes/chat.py` |
| GET | `/api/v1/chat/quota` | `routes/chat.py` |
| POST/GET/DELETE | `/api/v1/sessions[/{id}]` | `routes/sessions.py` |
| GET/DELETE | `/api/v1/memory` (memória de longo prazo do usuário) | `routes/memory.py` |
| GET | `/api/v1/rankings/{moradores,torres,me}` | `routes/ranking.py` |
| GET | `/api/v1/agents`, `/api/v1/costs` | `routes/agents.py`, `routes/observability.py` |
| — | `/a2a/jsonrpc/` + Agent Card | `integrations/a2a/server.py` |

### 1.3 Inicialização (`src/api/main.py`)

`create_app()` lê `Settings.from_env()` e, no `lifespan`:

1. Cria `AuthApiClient`/`CalendarApiClient` só se a URL estiver configurada.
2. Sobe Postgres (thread), Mongo + Redis (`SessionService.start`, com **fallback para memória** se `ALLOW_STORAGE_FALLBACK=true`), indexa o RAG (lê `data/FAQ_KNOWLEDGE_BASE.md` + página do SINIR, quebra em chunks, embeda, FAISS).
3. Neo4j é opcional: falha só gera aviso.
4. Monta `EcoGraphRuntime` (o grafo), `QuotaService`, `ChatService`, `HealthService` e pendura tudo em `app.state`.
5. No encerramento fecha clientes e faz `flush` do tracing.

### 1.4 Caminho de uma mensagem (`POST /api/v1/chat`)

```
request
  └─ get_user_context            (dependencies.py) Bearer → API de auth → UserContext
                                 (headers X-* só com APP_ENV=test + ALLOW_TEST_IDENTITY_HEADERS)
  └─ ChatService.chat            (chat_service.py)
       admit: rate/minuto → resolve sessão → teto da sessão → reserva cota diária
       lock da sessão (local + Redis SET NX PX, renovado)  → revalida teto
       append da mensagem do usuário
       conversation_trace + token em ContextVar
       └─ EcoGraphRuntime.invoke (graph.py, checkpointer thread_id=session_id)
            guardrail_entrada  regex (guardrails.py) + juiz_entrada (LLM)
              └ bloqueado → guardrail_saida
            memoria            resumo + janela recente + memória de longo prazo
            verificar_compactacao → resumir_memoria (se passou de MEMORY_MAX_MESSAGES)
            orquestrador       LLM escolhe rota → authorize_route (policies.py)
              ├ faq            RAG+LLM, ou Qdrant com resposta canônica (sem LLM)
              ├ educacional    RAG+LLM (+ tool A2A de agente externo, se configurado)
              ├ analytics      LLM + tools SQL/Redis dentro de tool_scope(usuário)
              ├ grafo          LLM + tools Cypher dentro de tool_scope(usuário)
              └ coletas        regras determinísticas → MCP → API de calendário
            juiz_saida         LLM julga rascunho contra a evidência (pula p/ Qdrant e coletas)
              └ reprovado → correcao (até JUDGE_MAX_CORRECTIONS) → juiz_saida
            guardrail_saida    usa resposta censurada se houver + sanitize_output (CPF/e-mail/segredos)
       append da resposta, última rota, flag de mensagem bloqueada
       erro → devolve a cota
  └─ ChatResponse (answer, sources, judge, usage, quota, latências)
```

Depois da resposta, `ChatService.remember` consolida a memória de longo prazo a cada `LONG_TERM_MEMORY_EVERY_TURNS` turnos e ao encerrar a sessão. Mensagens bloqueadas ou reprovadas no guardrail ficam de fora. A gravação usa compare-and-set por `revision`.

### 1.5 Camadas de segurança (em ordem)

1. **Identidade** fora do LLM: o token nunca vai para o estado do grafo. `UserContext.without_token()` entra no estado e o token fica em `ContextVar` (`request_context.py`).
2. **Guardrail regex** de entrada (injeção, dados de terceiros, fraude de ranking).
3. **Juiz de entrada** (LLM).
4. **Autorização por rota/ação** (`policies.py`): perfil × permissão.
5. **Escopo das tools** (`tool_scope.py` + `ToolScopeMiddleware`): os argumentos de identidade das tools são sobrescritos pelo usuário autenticado.
6. **Juiz de saída** (LLM) contra a evidência das tools/RAG.
7. **Guardrail de saída** regex + máscara de PII no tracing.

### 1.6 Modo mock

Com `LLM_PROVIDER=mock`, `AgentSuite` não cria modelos e responde por heurística de palavras-chave (`factory.py::_mock_response`). Com `EMBEDDING_PROVIDER=mock`, entra `HashEmbeddings`, e com `STORAGE_MODE=memory`, dicionários em processo. É assim que a suíte roda sem serviços externos. O código dos prompts reais **não** é importado nesse modo, e por isso existe `tests/test_imports.py`.

---

## 2. Pontos de melhoria (ponytail)

Ordenados pelo tamanho do corte. Tags: `delete` (remover), `shrink` (mesma lógica, menos linhas), `stdlib`/`native` (trocar por biblioteca padrão ou recurso já disponível), `yagni` (abstração sem uso real).

| # | Tag | O que cortar | Substituto | Arquivo(s) | Linhas |
|---|---|---|---|---|---|
| 1 | shrink | `Settings.from_env` repete os ~100 defaults do dataclass | laço em `dataclasses.fields()` + mapa para os 3 nomes de env diferentes (`APP_ENV`, `CALENDAR_API_BASE_URL`, `CALENDAR_API_TIMEOUT_SECONDS`) | `src/core/config.py` | ~-95 |
| 2 | delete | re-exports em `__init__.py` que ninguém importa | import direto do módulo | `src/database/`, `src/security/`, `src/integrations/auth/`, `src/integrations/calendar/`, `src/agents/coleta/`, `src/tools/shared/`, `src/shared/` | ~-75 |
| 3 | native | `build_chat_model`/`build_embeddings` com um `if` por provedor | `langchain.chat_models.init_chat_model` / `init_embeddings` (sobra só mock + checagem de chave) | `src/core/llm.py` | ~-55 |
| 4 | shrink | as duas tools MCP repetem a abertura (runtime, `not_configured`, token); `except` lista as 6 subclasses; `_build_filters` = `CalendarFilters(...)` | helper de abertura + `except CalendarApiError` | `src/integrations/mcp/server.py` | ~-45 |
| 5 | shrink | nós `analytics` e `grafo` montam o mesmo prompt; 3 dicts quase iguais de bypass no `juiz_saida` | `_tool_specialist(state, nome, instrução)` + `_bypass(motivo, candidato)` | `src/agents/graph.py` | ~-35 |
| 6 | shrink | `get_commits`/`get_files` com a mesma paginação; `github_get`/`github_patch` gêmeos | `_paginate(path)` + `_github(method, ...)` | `scripts/pr-bot/generate_pr.py` | ~-35 |
| 7 | shrink | `chat()` e `stream()` duplicam lock → append → trace → grafo → finish | núcleo comum | `src/services/chat_service.py` | ~-30 |
| 8 | delete | sem uso: `ServiceStatusResponse`, `UserContext.has_permission`, `ALL_PROFILES`, `get_rag_service`, `get_graph`, `ChatService.tracing`, `app.state.calendar_api/calendar_mcp/tracing`, `SessionService.close()` (no-op) | nada | vários | ~-30 |
| 9 | delete | `src/models/` (`nodes.py`, `relationships.py`), nunca importado | `src/etl/graph_model.py` já é a fonte | `src/models/` | ~-28 |
| 10 | native | `_message_text` e `_content_text` são cópias um do outro | `AIMessage.text` (langchain-core 1.x) | `src/agents/factory.py`, `src/services/memory_summarizer_service.py` | ~-22 |
| 11 | delete | `CalendarTools` só repassa as chamadas | usar `CalendarMcpClient` direto | `src/agents/coleta/tools.py` | ~-22 |
| 12 | stdlib | `_storage_error_types`/`_redis_errors` fazem `try: import ... except ImportError` de dependências obrigatórias | tuplas simples | `src/services/session_service.py`, `src/services/session_lock.py` | ~-20 |
| 13 | delete | `run_analytics` repete a checagem de mock e alimenta `TOOL_LATENCY` com 1 rótulo fixo (já coberto por `AGENT_LATENCY`); `parse_route` estático só repassa | chamar `invoke_with_tools`/`output_parsing.parse_route` | `src/agents/factory.py`, `src/observability/metrics.py` | ~-15 |
| 14 | yagni | demos `__main__` (`consultar_via_mcp`, `_main`) | nada | `src/integrations/mcp/client.py`, `src/integrations/a2a/client.py` | ~-15 |
| 15 | shrink | remoção de acentos (NFD + filtro `Mn`) copiada 5 vezes | um `fold()` compartilhado | `security/roles.py`, `security/policies.py`, `agents/coleta/agent.py`, `agents/analytics/tools/common.py`, `etl/populate_qdrant.py` | ~-12 |
| 16 | delete | config nunca lida: `AURA_INSTANCEID`, `AURA_INSTANCENAME`, `RUN_CALENDAR_INTEGRATION_TESTS` (só validada) | nada | `config.py`, `.env.example`, `k8s/configmap.yaml` | ~-10 |
| 17 | yagni | nó `verificar_compactacao` só decide uma aresta | aresta condicional saindo de `memoria` (muda `agents_called` e os rótulos de progresso) | `src/agents/graph.py` | ~-10 |
| 18 | stdlib | `RAGService._to_thread` embrulha `partial` à toa | `asyncio.to_thread(func, *args, **kwargs)` | `src/services/rag_service.py` | ~-6 |
| 19 | stdlib | `_message_date` com duas regex + `try` | laço de `datetime.strptime` nos 2 formatos | `src/agents/coleta/agent.py` | ~-6 |
| 20 | yagni | `Settings.with_overrides` = `dataclasses.replace`, só testes usam | `replace(settings, ...)` | `src/core/config.py` | ~-3 |
| 21 | native | `faiss-cpu` + `langchain-community` para um `FAISS.from_documents` de ~200 chunks | `langchain_core.vectorstores.InMemoryVectorStore` | `src/services/rag_service.py`, `requirements.txt` | -2 deps |
| 22 | stdlib | (opcional) `beautifulsoup4` só para extrair texto de uma página | `html.parser.HTMLParser` (~10 linhas) | `src/services/rag_service.py` | -1 dep |

**Total possível: ~-570 linhas, -3 dependências.**

Itens que mudam comportamento e pedem decisão antes:

- **#3** muda as mensagens de erro de provedor mal configurado.
- **#17** muda `agents_called`, os testes que o verificam e os eventos de progresso do SSE.
- **#21** troca o índice vetorial. O resultado deve ser equivalente (busca exata por cosseno), mas precisa ser validado.

---

## 3. Possíveis erros

Três revisões somente leitura, uma por área: agentes/segurança/chat, serviços/banco/API e integrações/ETL/config. Achados repetidos entre as áreas foram unidos. Conferi os números de linha com `grep` no commit `03f10e1` e reproduzi três achados à mão: o item M7 (`issubclass(ServiceUnavailable, Neo4jError) == False` no neo4j instalado), o M3 (marcadores de `coleta_gerenciar`) e o M11 (dupla contagem no middleware). Nenhum achado foi classificado como Alta. Não rodei código para reproduzir os demais.

Legenda: **M** = média, **B** = baixa.

### 3.1 Severidade média

| # | Problema | Arquivo:linha | Onde e quando surge | Correção sugerida |
|---|---|---|---|---|
| M1 | Checkpointer `InMemorySaver` cresce sem limite e guarda PII | `src/agents/graph.py:539` | Cada nó de cada turno grava o estado inteiro (memória, evidência, `user_context`) por `thread_id=session_id`. Nada remove esses checkpoints, nem quando a sessão expira. `_TURN_DEFAULTS` já reseta o estado, então o checkpointer não traz benefício funcional. | Compilar sem checkpointer, ou chamar `delete_thread(session_id)` ao fim do turno |
| M2 | Coletas usa o fuso do servidor para "hoje/amanhã" | `src/agents/coleta/agent.py:140` | `_period_from_message` com `date.today()`. Container em UTC às 22h de São Paulo → consulta o dia seguinte. O resto do projeto usa `America/Sao_Paulo`. | `datetime.now(SP_TZ).date()` |
| M3 | "recorrente"/"avulso" contam como **gerenciar** | `src/security/policies.py:146` | `action_for_route("coletas", ...)`. "Quais coletas recorrentes eu tenho?" vira `coleta_gerenciar`: o usuário comercial é negado e o síndico recebe "sem endpoint". O filtro `possui_recorrencia` (`agent.py`) nunca é alcançado por essa frase. | Deixar em gerenciar só verbos de mutação (agendar, remarcar, alterar) |
| M4 | Coletas repassa `condominio_id`/`cooperativa_id` digitados no texto | `src/agents/coleta/agent.py:124-129` | `_filters_from_message` extrai "condomínio 2" e envia à API com o token do síndico do condomínio 1. Se a API Java não validar o escopo pelo token, há leitura cruzada. As tools de analytics forçam o escopo (`scope_tool_args`); coletas não. | Usar `user.condominio_id`/`cooperativa_id`, ou rejeitar um id diferente do do usuário |
| M5 | `periodo` desconhecido vira consulta de todo o histórico | `src/agents/analytics/tools/common.py:29` | `resolve_period("ultimos_30_dias")` → `(None, None)`. A tool soma tudo e o agente pode apresentar o acumulado como "últimos 30 dias". O juiz aprova porque o número está na evidência. | Devolver erro de período inválido quando `periodo` for informado e não reconhecido |
| M6 | Neo4j: defaults divergentes e credencial padrão | `src/core/config.py:81` (dataclass `None`) × `:246-249` (`from_env`: `bolt://localhost:7687`, `neo4j`/`neo4j`) | Sem `NEO4J_*` (o configmap do k8s não define), o app tenta localhost com a senha padrão. Com `Settings()` direto, `driver(None)` levanta `TypeError`. O estado `not_configured` do health é inalcançável. | Default `None` nos dois lugares e só chamar `neo4j_db.start` se houver URI. **A Task 1 do plano preserva o comportamento atual; esta correção é separada.** |
| M7 | Neo4j fora do ar derruba a tool do grafo | `src/database/neo4j.py:25` e `src/agents/graph_traversal/tools.py:112,164,198` | `start()` atribui `self.client` antes de `verify_connectivity()`. Se falhar, o cliente fica "meio iniciado" e nunca reconecta. A tool captura só `RuntimeError`/`Neo4jError`, e `ServiceUnavailable` é `DriverError`: a exceção sobe em vez de virar "indisponível". | Atribuir o cliente só depois da conexão; capturar `neo4j.exceptions.DriverError` |
| M8 | CronJobs do ETL rodam juntos às 03:00 | `k8s/graph-etl-cronjobs.yaml:5,17` | `concurrencyPolicy: Forbid` vale por CronJob. O incremental (`*/15`) e o full (`0 3`, com `--prune`) são objetos distintos e escrevem no grafo ao mesmo tempo (deadlock de MERGE). O comentário da linha 5 afirma o contrário. | Tirar a janela das 3h do incremental, ou lock no `EtlCheckpoint` |
| M9 | Manifests k8s sem variáveis que o app exige | `k8s/configmap.yaml`, `k8s/secret.example.yaml`, `k8s/ia-deployment.yaml:44` | Faltam `AUTH_API_URL` (sem ela, toda rota autenticada responde 503), `CALENDAR_API_BASE_URL`, `QDRANT_URL`, `REDIS_URL`, `OLLAMA_BASE_URL` e `A2A_PUBLIC_URL`. Sem esta última, o Agent Card anuncia `127.0.0.1`. O `APP_VERSION` do configmap diverge da tag da imagem. | Completar o configmap/secret de exemplo, marcando obrigatória/opcional |
| M10 | `docker compose` com `.env` copiado do exemplo não conecta | `docker-compose.yml:7` × `.env.example` | Dentro do container, `localhost` aponta para o próprio container. O compose só sobrescreve `REDIS_URL`/`LANGFUSE_HOST`. Mongo, Postgres, Ollama e Neo4j falham, e o serviço `graph-etl` não tem `environment`. | Documentar `host.docker.internal` ou sobrescrever no compose |
| M11 | Métricas HTTP: cardinalidade ilimitada e erro contado 2× | `src/observability/middleware.py:27,33-34,44-45` | `route_label = request.url.path`: cada `/sessions/<uuid>` e cada 404 aleatório cria uma série nova no Prometheus. Numa exceção, `REQUEST_ERRORS` sobe no `except` e de novo no `finally`. | Usar o template da rota (`request.scope["route"].path`) com fallback `"unmatched"`; contar só no `finally` |
| M12 | `/api/v1/costs` sem autenticação | `src/api/routes/observability.py:15` | Qualquer cliente lê preços, custo de infra, benefício e ROI. | Exigir identidade/perfil administrativo, ou restringir por rede |
| M13 | Servidor A2A público sem limite | `src/integrations/a2a/server.py:51` | `/a2a/jsonrpc/` não tem auth nem rate limit, não passa por `validate_input` e não tem teto de tamanho. Cada chamada gera embedding (custo) fora da cota e do ledger de custo. | Limitar o tamanho da pergunta, aplicar rate limit ou token compartilhado, ou flag para desligar |
| M14 | Pergunta enviada ao agente A2A externo só tem CPF/e-mail redigidos | `src/integrations/a2a/tool.py:10` (`sanitize_output`) | Telefone, CNPJ, CEP, RG e JWT/Bearer passam. O comentário da tool diz que nenhum dado pessoal sai. `tracing.mask_text` já cobre esses padrões. | Aplicar `mask_text` antes do envio |
| M15 | Cota não volta quando o cliente fecha o SSE | `src/services/chat_service.py:299-307` | `stream()` captura só `Exception`; a desconexão é `CancelledError`/`GeneratorExit` (`BaseException`). A cota fica consumida e a mensagem do usuário fica gravada sem resposta. | `finally` com flag "respondido" e `_refund` sob `asyncio.shield` |
| M16 | Redis fora derruba o chat por causa do `session:ptr` | `src/services/session_service.py:159` (`create_session`) e no `append_message` (`:190`) | O ponteiro é gravado sem `try` **depois** do Mongo. Lock e cota foram feitos para degradar sem Redis, mas aqui sai um 500 e fica uma sessão órfã. O ponteiro nunca é lido; só é apagado em `close_session`. | Remover o ponteiro, ou `try/except RedisError` com log |
| M17 | `DELETE /memory` não cria tombstone para usuário novo | `src/services/session_service.py:419` | `update_one` sem `upsert`. Uma consolidação em andamento com `expected_revision=0` faz `insert_one` logo depois e grava exatamente o que o usuário pediu para apagar (LGPD). | `upsert=True` (e equivalente no modo memória) |
| M18 | `/health` diz "ok" em fallback para memória | `src/services/session_service.py:467` + `src/api/routes/health.py` | Com `ALLOW_STORAGE_FALLBACK=true` e Mongo/Redis fora, as sessões ficam voláteis e não são compartilhadas entre réplicas, mas o status continua `ok`, porque só `"error"` degrada. | Tratar `memory_fallback` como degradado quando `STORAGE_MODE != memory` |
| M19 | Dicts de lock nunca limpos | `src/services/session_service.py:260`, `src/services/chat_service.py:159` | `_compaction_locks` só é limpo no `DELETE` da sessão (sessões que expiram por TTL ficam para sempre). `_remember_locks` nunca é limpo. Além disso, `close_session` remove um lock que outra corrotina pode estar segurando. | Reaproveitar `_LocalLocks` (refcount) de `session_lock.py` |

### 3.2 Severidade baixa

| # | Problema | Arquivo:linha | Onde e quando surge | Correção sugerida |
|---|---|---|---|---|
| B1 | Listagem de coletas esconde itens | `src/agents/coleta/agent.py:73-76` | Mostra só 5 e só avisa se `total_elements > len(página)`. Uma página com 8 de 8 exibe 5 sem aviso, e o juiz não roda em coletas. | "mostrando 5 de N" |
| B2 | Blocos sem `text` viram `repr` de dict na resposta | `src/agents/factory.py:125`, `src/services/memory_summarizer_service.py:60` | Um bloco `thinking` (ou texto vazio) cai em `str(part)` e vaza o raciocínio para o juiz, o usuário e o resumo. | Aceitar só blocos `type == "text"`. **Resolvido junto com a Task 5/7 do plano (`AIMessage.text`).** |
| B3 | `route` do turno anterior sobrevive num turno bloqueado | `src/agents/graph.py:545` + `src/services/chat_service.py:96` | `_TURN_DEFAULTS` não zera `route`. Um bloqueio no turno 2 grava `ultima_rota` do turno 1. | `"route": ""` nos defaults |
| B4 | O prompt do orquestrador manda chamar `obter_memoria()`, que não existe | `src/prompts/shared/orchestrator.py:12` | O agente é criado sem tools e a memória vai no prompt. O prompt também pede `ultima_rota`, que nunca é enviada. Continuações ("sim, continua") caem em `faq`. | Tirar a instrução e enviar `ULTIMA_ROTA=` |
| B5 | Compactação resume mensagens bloqueadas | `src/services/memory_summarizer_service.py:31` | `to_compact` inclui `bloqueada=True`. Uma injeção bloqueada vira "MEMÓRIA CONSOLIDADA" em todos os prompts seguintes. O `remember` filtra essas mensagens; a compactação não. | Filtrar `bloqueada` antes de resumir |
| B6 | `permissoes: []` = sem restrição | `src/security/policies.py:173` | `if ... and user.permissoes` trata a lista vazia (todas as permissões revogadas) como "não informado", o que libera o teto do perfil. | Distinguir `None` de `[]` |
| B7 | Qualquer número de 11 dígitos vira "[CPF REDIGIDO]" | `src/security/guardrails.py:37` | Um telefone com DDD sem formatação, ou um id longo, numa resposta de analytics/calendário é redigido. | Validar o dígito verificador (já existe `_cpf_valido` em `tracing.py`) |
| B8 | Modo memória: sessões nunca expiram e o dict de cota só cresce | `src/services/session_service.py:176`, `src/services/quota_service.py:84` | `get_session` ignora `expira_em`. Chaves `quota:rate:*` antigas nunca são removidas. | Checar `expira_em` e podar as entradas |
| B9 | 429 de cota diária deixa sessão órfã | `src/services/chat_service.py:66` | `admit` cria a sessão antes de `reserve_daily`. | Reservar antes, ou apagar a sessão nova se a admissão falhar |
| B10 | Rotas de sessão devolvem 500 em vez de 503 | `src/api/routes/sessions.py:43` | Só `SessionForbidden`/`NotFound` são mapeados; `StorageUnavailable` e `PyMongoError` saem crus. | Handler global → 503 |
| B11 | `append_message`/`update_last_route` não checam `matched_count` | `src/services/session_service.py:190` | Uma sessão expirada ou apagada (o `DELETE` não usa `conversation_guard`) entre o `get_session` e o `$push` → resposta entregue sem persistir. | Checar `matched_count`; `close_session` sob o guard |
| B12 | Sessão vencida segue ativa até o monitor de TTL do Mongo | `src/services/session_service.py:176` | O índice TTL roda a cada ~60 s; `get_session` não compara `expira_em`. | Comparar com o agora (ver B13) |
| B13 | Datetimes naive (Mongo) × aware | `src/database/mongodb.py:19` | `POST /sessions` responde com offset; `GET` responde sem. Qualquer comparação futura com `_now()` levanta `TypeError`. | `AsyncMongoClient(..., tz_aware=True)` |
| B14 | Estorno de cota cria contador negativo sem TTL | `src/services/quota_service.py:166` | O INCR caiu em memória (Redis fora); o Redis volta; o DECR cria `-1` permanente (+1 de cota). | Guardar em `QuotaStatus` qual backend contou |
| B15 | `lifespan`: um `close()` com erro impede os outros | `src/api/main.py:120-132` | `finally` sequencial sem proteção individual. | `AsyncExitStack` |
| B16 | `X-Request-ID` aceito sem validação | `src/observability/middleware.py:24` | Vai para logs, traces e resposta; permite log injection/inflação. | `^[A-Za-z0-9._-]{1,64}$`, senão UUID |
| B17 | `int(qdrant_timeout)` zera timeout fracionário | `src/services/qdrant_service.py:53`, `src/etl/populate_qdrant.py:273` | `QDRANT_TIMEOUT=0.5` → 0. | Passar o float ou `max(1, ceil(...))` |
| B18 | MCP lê o `settings` global | `src/integrations/mcp/server.py:125` | `consultar_guia_ecociente` ignora `create_app(settings_override=...)` e relê o arquivo a cada chamada. | Settings pelo runtime; cachear as seções |
| B19 | Máscara de PII por igualdade exata de chave | `src/observability/tracing.py:94` | `access_token`, `x-api-key`, `cookie`, `password_hash` e `endereco` passam. Um `namedtuple` quebra em `type(data)(items)`. | Casar por regex; `data._make` |
| B20 | `Settings` expõe segredos no `repr` | `src/core/config.py:36` | Um `logger.debug(settings)` ou um diff do pytest imprime URLs com senha e chaves de API. | `field(repr=False)` nas credenciais |
| B21 | `HOST`/`PORT` não têm efeito | `src/core/config.py:41` + `Dockerfile` | Nada lê esses campos; o CMD do uvicorn é fixo em `0.0.0.0:8000`. | Usar `${PORT}` no CMD ou remover as variáveis |
| B22 | pr-bot quebra com mensagem de commit vazia e não redige título/commits | `scripts/pr-bot/generate_pr.py:314` | `.splitlines()[0]` → `IndexError`. Só os patches passam por `redact_secrets`. | `(msg.splitlines() or [""])[0]` e redigir tudo |
| B23 | pr-bot sobrescreve edição feita durante a execução | `scripts/pr-bot/generate_pr.py:670` | O body é lido antes da chamada ao Gemini e gravado depois. Um marcador ou `@menção` gerado pelo modelo não é neutralizado. | Reler a PR antes do PATCH; escapar marcadores e `@` |

### 3.3 Verificado sem problema

- Os retries de auth e calendário só repetem GET (idempotente).
- O Cypher do ETL usa labels e tipos de constantes, com valores sempre por `$param`/`UNWIND`.
- O checkpoint do ETL grava o início da execução, só em sucesso, com overlap de 10 min; as colunas são `TIMESTAMPTZ`.
- A matemática de custo/ROI confere, com divisões protegidas.
- O cliente A2A compara esquema+host do Agent Card e falha fechado.
- Os headers de identidade de teste exigem `APP_ENV=test` e a flag; `require_same_user` está correto nas rotas.

### 3.4 Prioridade sugerida

1. **Segurança/LGPD:** M4, M12, M13, M14, M17.
2. **Disponibilidade:** M7, M9, M15, M16, M18.
3. **Respostas erradas ao usuário:** M2, M3, M5, B1, B3, B7.
4. **Vazamentos lentos de memória:** M1, M11, M19, B8.
5. **Operação:** M8, M10, B15, B21 e o restante.
