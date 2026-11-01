# Base de conhecimento no Qdrant (sem indexar na subida da API) — plano

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** o Qdrant passa a ser a base de conhecimento padrão: FAQ canônico **e** trechos do RAG. A API para de ler o `.md`, baixar o SINIR e gerar embeddings toda vez que sobe. A indexação passa a acontecer só quando alguém roda `python -m src.etl.populate_qdrant`.

**Architecture:**
- O `populate_qdrant.py` passa a gravar dois tipos de ponto na mesma coleção:
  - `tipo=faq_canonica`, que já existe;
  - `tipo=rag_trecho`, o novo: o `.md` inteiro e o SINIR, quebrados com o mesmo splitter do `RAGService`.
- O `QdrantFaqService` filtra cada busca por `tipo` e ganha `buscar_trechos`.
- O `RAGService`, quando `FAQ_BACKEND=qdrant`, não indexa nada no `start()` e o `search()` consulta o Qdrant.
- `FAQ_BACKEND=rag` continua existindo e mantém o comportamento atual (tudo em memória), para testes e para desenvolvimento sem Qdrant.

**Tech Stack:** Python 3.11, `qdrant_client` (async), fastembed via `EmbeddingService`, LangChain `RecursiveCharacterTextSplitter`, pytest com `AsyncQdrantClient(location=":memory:")`.

**Spec:** pedido do usuário em 30/10/2026: "não quero que ele rode o script de popular o qdrant toda vez; quero que use o qdrant por padrão em vez da base .md". Ele escolheu a opção "Qdrant para tudo".

## Global Constraints

- **Sem git**: nenhum `git add/commit/stash/checkout/push/reset/worktree`. O usuário faz os commits.
- **Nunca matar processos.**
- Suíte (Git Bash): `LLM_PROVIDER=mock EMBEDDING_PROVIDER=mock STORAGE_MODE=memory ENABLE_EXTERNAL_SOURCE=false FAQ_BACKEND=rag .venv/Scripts/pytest -q -p no:cacheprovider`.
  - **Não** usar `python -m pytest`: o `pytest.py` da raiz (ignorado pelo git) toma o lugar do módulo.
  - Hoje a `.venv` (Python 3.14, Windows) não tem `tzdata`, e cerca de 74 testes falham com `ZoneInfoNotFoundError`. Antes da Task 1, o usuário decide se roda `.venv/Scripts/pip install tzdata`. Sem isso, comparar só "falhas que não são de fuso" antes e depois.
- Um único interruptor: `FAQ_BACKEND`. `qdrant` é o novo padrão (FAQ e trechos vêm do Qdrant); `rag` mantém tudo em memória. **Nenhuma variável nova.**
- O embedding dos trechos usa `QDRANT_EMBEDDING_MODEL` (o mesmo do FAQ), com os prefixos `passage:`/`query:` que o `EmbeddingService` já aplica.
- Sem dependências novas. Estilo do código vizinho: comentários em português.

## Review Focus

1. **Busca do FAQ trazendo trechos:** com os dois tipos na mesma coleção, `buscar_faq` precisa filtrar `tipo=faq_canonica`; sem isso, trechos ocupam o `top_k`. Fica fixado na Task 2.
2. **Qdrant fora do ar ou coleção sem trechos:** o `educacional` responde "A base consultada não contém evidência suficiente…" (mesmo texto de hoje para contexto vazio), não derruba o chat, e o `/health` mostra `rag: "error"`. Fica fixado na Task 2.
3. **`.md` editado sem rodar a carga:** o chat continua com os trechos antigos (esperado). Rodar a carga reindexa só os trechos alterados e apaga os que sumiram. Fica fixado na Task 1.
4. **SINIR fora do ar durante a carga:** a carga segue só com o `.md` e não apaga os trechos do SINIR já indexados. Fica fixado na Task 1.
5. **Testes e desenvolvimento sem Qdrant:** com `FAQ_BACKEND=rag`, nada muda em relação a hoje. Fica fixado na Task 3 (a suíte inteira roda assim).

---

### Task 1: A carga indexa também os trechos do RAG

**Files:**
- Modify: `src/etl/populate_qdrant.py`
- Modify: `src/services/rag_service.py` (só extrair o que a carga reusa: o splitter e a leitura do SINIR viram funções de módulo)
- Test: `tests/test_populate_qdrant.py`

**Interfaces:**
- Produces, em `src/services/rag_service.py`:
  - `dividir_em_trechos(texto: str, *, title: str, source: str, url: str | None, kind: str, settings: Settings) -> list[Document]`, com os mesmos `rag_chunk_size`/`rag_chunk_overlap` e o `metadata["chunk"]` de hoje;
  - `async baixar_fonte_externa(settings: Settings) -> str`, que é o `_fetch_external` atual movido para fora da classe.
  - O `RAGService.start()` em memória passa a usar essas duas funções (comportamento igual).
- Produces, em `src/etl/populate_qdrant.py`:
  - `TIPO_TRECHO = "rag_trecho"`;
  - `@dataclass Trecho(title, source, url, kind, chunk: int, texto: str)` com `point_id = uuid5(_NAMESPACE, f"trecho:{source}#{chunk}")` e `payload(embedding_model) -> dict`. O payload tem as chaves `tipo, title, source, url, kind, chunk, texto, embedding_model, content_hash`;
  - `async QdrantFaqLoader.carregar_trechos(trechos: list[Trecho], *, fontes: set[str], recriar=False, batch_size=32) -> CargaResultado`. Ele remove os pontos `rag_trecho` cujo `source` está em `fontes` e que não vieram nesta carga; um `source` fora de `fontes` não é tocado.
  - `main()`:
    - lê o `.md`, monta os FAQs e os trechos;
    - baixa o SINIR quando `ENABLE_EXTERNAL_SOURCE=true` e, se a baixada falhar, registra um warning e tira o SINIR de `fontes`;
    - carrega primeiro os FAQs e depois os trechos;
    - imprime os dois resumos;
    - o `--dry-run` mostra também quantos trechos sairiam.

- [ ] **Step 1: Escrever os testes que falham** (`tests/test_populate_qdrant.py`, reaproveitando `FakeEmbedder` e o cliente `:memory:`):
  - `test_knowledge_base_becomes_rag_chunks_with_stable_ids`: o `.md` real vira mais de 0 trechos; todos têm `kind == "local"`; duas montagens geram a mesma lista de `point_id`.
  - `test_chunks_load_incrementally_and_drop_removed_ones`:
    - 1ª carga: `inseridos == n`;
    - 2ª carga igual: `inalterados == n` e o embedder não é chamado;
    - 3ª carga com um trecho alterado e o último removido: `atualizados == 1` e `removidos == 1`.
  - `test_chunks_never_touch_canonical_faqs_or_other_sources`:
    - carregar FAQs e depois trechos de `fontes={"md"}`: o número de pontos `faq_canonica` não muda;
    - um trecho com `source="sinir"` já indexado continua lá quando `fontes={"md"}`.
- [ ] **Step 2: Rodar e ver falhar.** `.venv/Scripts/pytest tests/test_populate_qdrant.py -q`. Esperado: falha com ImportError de `Trecho`/`TIPO_TRECHO`.
- [ ] **Step 3: Implementar** as interfaces acima. `carregar_trechos` repete a lógica de `carregar` (hash no payload, embeddings em lote, criação da coleção e do índice `tipo`). Fatorar o trecho comum num método privado só se isso deixar o arquivo menor.
- [ ] **Step 4: Rodar os testes do arquivo e os de `tests/test_rag_text.py`.** Esperado: PASS.

---

### Task 2: RAG e FAQ consultam o Qdrant, e a API não indexa nada ao subir

**Files:**
- Modify: `src/services/qdrant_service.py`
- Modify: `src/services/rag_service.py`
- Modify: `src/api/main.py:62-72`
- Test: `tests/test_faq_qdrant.py`

**Interfaces:**
- Consumes: os pontos `rag_trecho` da Task 1 e o payload descrito lá.
- Produces:
  - `QdrantFaqService.buscar_faq(...)` passa a filtrar `tipo=faq_canonica` (via `query_filter` no `query_points`);
  - `async QdrantFaqService.buscar_trechos(pergunta: str, limite: int) -> list[dict]`, que devolve `{title, source, url, chunk, texto, score}` filtrando `tipo=rag_trecho`;
  - `async QdrantFaqService.contar(tipo: str) -> int`;
  - `RAGService(settings, *, qdrant: QdrantFaqService | None = None)`. Com `settings.faq_backend == "qdrant"` e `qdrant` informado:
    - `start()` não lê arquivo, não baixa nada e não gera embedding;
    - `search(query, k)` chama `buscar_trechos` e monta `context` e `sources` no **mesmo formato** de hoje (`[Fonte: <title> | chunk <n>]\n<texto>` e `SourceResponse`);
    - se o Qdrant falhar, registra um warning e devolve `("", [])`.
  - `RAGService.health()` vira async: com o Qdrant, devolve `"ok"` se `contar("rag_trecho") > 0` e `"error"` caso contrário (ou se o Qdrant falhar); em memória, mantém os valores de hoje. Ajustar a chamada em `src/services/health_service.py:40` com `await`.
  - `main.py`: criar o `QdrantFaqService` antes do `RAGService` e passá-lo como `qdrant=`, para que o modelo de embedding seja carregado uma vez só.

- [ ] **Step 1: Escrever os testes que falham** em `tests/test_faq_qdrant.py`, com cliente `:memory:` e `FakeEmbedder`, gravando pontos dos dois tipos:
  - `test_faq_search_ignores_rag_chunks`: um trecho com texto idêntico à pergunta não aparece em `buscar_faq`.
  - `test_rag_search_reads_chunks_from_qdrant_in_the_same_format`: com `faq_backend="qdrant"`, o `RAGService.search` devolve um `context` que começa com `"[Fonte: FAQ EcoCiente | chunk 0]"` e `sources[0].source` igual ao `source` do payload.
  - `test_rag_start_does_not_index_when_backend_is_qdrant`: fazer monkeypatch de `InMemoryVectorStore.from_documents` e de `Path.read_text` para lançar erro. `await RAGService(..., qdrant=svc).start()` não pode lançar.
  - `test_rag_search_degrades_when_qdrant_is_down`: cliente que lança erro em `query_points`. O `search` devolve `("", [])` e o `health()` devolve `"error"`.
- [ ] **Step 2: Rodar e ver falhar.** `.venv/Scripts/pytest tests/test_faq_qdrant.py -q`.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar `tests/test_faq_qdrant.py`, `tests/test_populate_qdrant.py` e `tests/test_api.py`.** Esperado: PASS, exceto falhas de fuso já conhecidas.

---

### Task 3: Qdrant como padrão, testes fixados em memória e documentação

**Files:**
- Modify: `src/core/config.py:91` (`faq_backend: str = "qdrant"`)
- Modify: `tests/conftest.py` (`faq_backend="rag"` nas overrides do fixture `client`)
- Modify: `.github/workflows/ci.yml` (env `FAQ_BACKEND: rag`)
- Modify: `.env.example` (`FAQ_BACKEND=qdrant` e comentário que explica que a carga é manual e o que `rag` faz)
- Modify: `k8s/configmap.yaml:49-51` (`FAQ_BACKEND: "qdrant"`; o comentário passa a citar `QDRANT_URL` no Secret e a carga manual)
- Modify: `README.md` / `README-en.md`: tabela de variáveis, Pré-requisitos (Qdrant obrigatório no padrão), seção "FAQ via Qdrant" (vira "Base de conhecimento no Qdrant", dizendo quando rodar a carga: ao editar o `.md` ou para atualizar o SINIR) e o comando de testes com `FAQ_BACKEND=rag`
- Modify: `docs/ARQUITETURA.md`, se descrever a indexação na subida
- Test: a suíte inteira

**Interfaces:**
- Consumes: Tasks 1 e 2.

- [ ] **Step 1: Teste de padrão.** Em `tests/test_faq_qdrant.py`, renomear `test_rag_backend_is_untouched_by_default` para `test_qdrant_is_the_default_backend`, com `assert Settings().faq_backend == "qdrant"`. O teste antigo que garantia `rag` como padrão passa a ser este.
- [ ] **Step 2: Mudar os padrões e os arquivos de configuração listados.**
- [ ] **Step 3: Rodar a suíte inteira** (comando em Global Constraints). Testes que montam `Settings(...)` direto e sobem a app sem passar pelo `conftest` vão cair no Qdrant: adicionar `faq_backend="rag"` neles. Não pode haver falha nova que não seja de fuso.
- [ ] **Step 4: Relatar** a contagem final e a lista do que ficou para o usuário fazer: rodar `python -m src.etl.populate_qdrant` uma vez no ambiente dele e configurar `QDRANT_URL` no Secret do k8s.

---

## Fora do plano

- Rodar a carga automaticamente (no startup, em CronJob ou na CI): o usuário não quer.
- `EMBEDDING_PROVIDER`/`EMBEDDING_MODEL` continuam valendo só para `FAQ_BACKEND=rag`. Não serão removidos agora.
- O guia do MCP (`consultar_guia_ecociente`) não usa o `RAGService` e não muda.
