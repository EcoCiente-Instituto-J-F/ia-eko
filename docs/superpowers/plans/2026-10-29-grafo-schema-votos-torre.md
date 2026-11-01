# Grafo: schema novo, votos por tipo, torre do morador e recomendações — plano

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** alinhar o ETL PostgreSQL → Neo4j e o agente `grafo` ao DDL mais recente, corrigindo a classificação de votos, levando a torre do morador para o grafo, entregando a tool de recomendações que o README já anuncia e aplicando os cortes da auditoria ponytail do grafo.

**Architecture:** o ETL continua sendo `Step` (SQL de extração + Cypher de `MERGE`) em `src/etl/populate_graph.py`, com o vocabulário em `src/etl/graph_model.py` e a conferência Postgres×grafo em `src/etl/validate_graph.py`. As tools do agente ficam em `src/agents/graph_traversal/tools.py`, liberadas por `TOOL_ACTIONS` em `src/security/tool_scope.py`. Nada de módulo novo.

**Tech Stack:** Python 3.11, psycopg2, driver `neo4j` async, LangChain `@tool` + Pydantic, pytest.

**Spec:** a conversa de 2026-10-29 (auditoria das queries do grafo + ponytail ultra). As decisões tomadas pelo usuário estão na seção "Decisões". O DDL de entrada fica em `C:\Users\HEITOR~1\AppData\Local\Temp\claude\C--Users-heitormiasato-ieg-Documents---Fora-do-OneDrive-Eko\47d1b2fc-1ed2-4c21-9d49-7806a907b21f\scratchpad\ecociente_schema_colado.sql` (cópia literal do que o usuário colou).

## Decisões (do usuário)

- `tb_quizzes.curso_id` e `tb_aulas.curso_id` são intencionais. O ETL continua usando `q.curso_id`. **Não mexer.**
- Torre do morador: nova aresta `(Usuario)-[:MORA_NA]->(Torre)` a partir de `tb_moradores.torre_id`.
- Recomendação: criar a tool `recomendacoes_curso`.
- Schema: a Task 1 grava o DDL colado em `sql/ecociente_schema.sql`, corrigindo a ordem do índice.
- Os cortes do ponytail entram na última task.

## Global Constraints

- **Sem git**: nenhum `git add/commit/stash/checkout/push/reset/worktree`. O usuário faz os commits. Só leitura é permitida (`git diff`, `git status`, `git log`). Mesma regra de `docs/superpowers/plans/2026-10-20-ponytail-audit.md`.
- **Nunca matar processos** (`taskkill`, `kill`, `pkill`, `Stop-Process`). A suíte demora alguns minutos: esperar.
- Suíte (Git Bash): `LLM_PROVIDER=mock EMBEDDING_PROVIDER=mock STORAGE_MODE=memory ENABLE_EXTERNAL_SOURCE=false "$LOCALAPPDATA/Temp/ia-eko-venv/Scripts/python" -m pytest -q -p no:cacheprovider`. Antes da Task 1, rodar uma vez e anotar a contagem de passed/skipped como referência.
- `tests/test_graph_etl_postgres.py` só roda com Postgres (binários locais em Linux ou `TEST_POSTGRES_URL`). No Windows sem isso, fica **skipped**: relatar "skipped" e não dizer que passou. No CI (ubuntu) ele roda.
- Nomes de lookup vêm do DDL: tipos de voto `aprovar` e `denunciar`.
- Estilo do código vizinho: comentários em português, mesma densidade, sem docstrings novas desnecessárias.
- Sem dependências novas.
- Nome de `Usuario` nunca sai de uma tool do grafo (é dado pessoal de terceiro).

## Review Focus

1. **Morador sem torre, ou que perdeu a torre** (`torre_id` NULL): nenhuma `MORA_NA` é criada e a antiga é apagada. Fica fixado na Task 3 (Cypher + integração).
2. **Denúncia gravada sem motivo** (insert direto, seed, RPA): vira `DENUNCIOU` com `motivo` nulo, e não `VALIDOU`. **Aprovação com motivo** vira `VALIDOU`. Fica fixado na Task 2 (integração).
3. **Recomendação de outra pessoa**: `recomendacoes_curso` não aceita `usuario_id` e sempre usa o `$uid` do escopo. Fica fixado na Task 4.
4. **Nome de morador vazando por `conexoes_diretas`**: vizinhos `Usuario` voltam com `nome` nulo. Fica fixado na Task 3.
5. **DDL com índice criado antes da tabela**: um teste estático impede a regressão. Fica fixado na Task 1.

Limitação conhecida, sem teste (já documentada no cabeçalho do ETL): voto que muda de tipo por UPDATE não altera `votado_em`, então só o full diário corrige.

---

### Task 1: Schema novo no repo e seed coerente com ele

**Files:**
- Modify: `sql/ecociente_schema.sql` (substituído inteiro)
- Modify: `tests/fixtures/etl_seed.sql:11,34-35,70-72`
- Test: `tests/test_graph_etl.py`

**Interfaces:**
- Produces: `sql/ecociente_schema.sql` com `tb_moradores.torre_id`, que a Task 3 lê. O seed passa a ter tipos de voto `aprovar` (id 1) e `denunciar` (id 2), usados pela Task 2.

- [ ] **Step 1: Escrever o teste que falha** em `tests/test_graph_etl.py`, perto de `_schema_columns`:

```python
def test_schema_creates_every_index_after_its_table():
    text = SCHEMA.read_text(encoding="utf-8")
    tabelas = {m.group(1).lower(): m.start() for m in re.finditer(
        r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?(tb_[a-z0-9_]+)", text, re.I)}
    fora_de_ordem = [
        (m.group(1), m.group(2))
        for m in re.finditer(r"CREATE (?:UNIQUE )?INDEX\s+(?:IF NOT EXISTS\s+)?(\w+)\s+ON\s+(tb_[a-z0-9_]+)", text, re.I)
        if tabelas.get(m.group(2).lower(), len(text)) > m.start()
    ]
    assert fora_de_ordem == []
```

- [ ] **Step 2: Copiar o DDL colado e confirmar que o teste falha.** Copiar `ecociente_schema_colado.sql` (caminho em **Spec**) por cima de `sql/ecociente_schema.sql`, byte a byte. Rodar `pytest tests/test_graph_etl.py::test_schema_creates_every_index_after_its_table -q`. Resultado esperado: FAIL, listando `('idx_esqueci_senha_usuario_id', 'tb_recuperacoes_senha')`.

- [ ] **Step 3: Corrigir a ordem.** Tirar o bloco `CREATE INDEX idx_esqueci_senha_usuario_id ON tb_recuperacoes_senha (usuario_id);` da Seção 9 e colocá-lo logo depois do `CREATE TABLE tb_recuperacoes_senha (...);` da Seção 15. Nenhuma outra mudança no DDL.

- [ ] **Step 4: Ajustar o seed** (`tests/fixtures/etl_seed.sql`):
  - linha 11: `INSERT INTO tb_lkp_tipos_votos_postagens (nome_tipo) VALUES ('aprovar'),('denunciar');`
  - `tb_moradores` (linhas 34-35): incluir `torre_id`. Ele fica NULL quando `id_usuario % 5 = 0`. Nos outros casos, recebe a torre `(SELECT min(id_torre) FROM tb_torres t WHERE t.condominio_id = <condomínio da linha>) + (id_usuario % 4)`. A FK composta exige torre do mesmo condomínio.
  - votos (linhas 70-72): o motivo só existe quando o tipo é `denunciar`. Ou seja, `tipo_voto_id = 1 + (k % 2)` e `motivo_denuncia_id = CASE WHEN k % 2 = 1 THEN 1 + (k % 3) END`.

- [ ] **Step 5: Rodar os testes do schema e do contrato SQL**

Run: `pytest tests/test_graph_etl.py tests/test_sql_contract.py tests/test_graph_etl_postgres.py -q`
Expected: os dois primeiros PASS. Se `test_sql_contract.py` falhar, é porque uma tool de analytics referencia coluna que o DDL novo não tem: **parar e relatar**, sem editar o DDL. `test_graph_etl_postgres.py` dá PASS com Postgres ou SKIPPED sem ele.

- [ ] **Step 6: Suíte inteira verde** (comando em Global Constraints). A contagem deve ser a referência + 1 passed.

---

### Task 2: VALIDOU/DENUNCIOU pelo tipo do voto

**Files:**
- Modify: `src/etl/populate_graph.py` (passos `validou` e `denunciou`, SQL full e incremental; tabela do docstring)
- Modify: `src/etl/validate_graph.py` (`CONTAGENS["VALIDOU"]` e `["DENUNCIOU"]`)
- Test: `tests/test_graph_etl.py`, `tests/test_graph_etl_postgres.py`

**Interfaces:**
- Consumes: os tipos `aprovar`/`denunciar` do seed (Task 1).
- Produces: o formato das linhas não muda. `validou` continua com `{usuario_id, postagem_id, peso, tipo}` e `denunciou` com `{usuario_id, postagem_id, peso, motivo}`, onde `motivo` pode ser `None`.

- [ ] **Step 1: Escrever o teste unitário que falha** em `tests/test_graph_etl.py`:

```python
def test_votes_are_classified_by_vote_type_not_by_reason():
    from src.etl.validate_graph import CONTAGENS

    steps = {s.name: s for s in EDGE_STEPS}
    for nome, tipo in (("validou", "aprovar"), ("denunciou", "denunciar")):
        for sql in _all_sql(steps[nome]):
            assert f"t.nome_tipo = '{tipo}'" in sql
            assert "motivo_denuncia_id IS" not in sql
    assert all("LEFT JOIN tb_lkp_motivos_denuncia" in sql for sql in _all_sql(steps["denunciou"]))
    assert "nome_tipo = 'aprovar'" in CONTAGENS["VALIDOU"][0]
    assert "nome_tipo = 'denunciar'" in CONTAGENS["DENUNCIOU"][0]
```

- [ ] **Step 2: Rodar e ver falhar.** `pytest tests/test_graph_etl.py::test_votes_are_classified_by_vote_type_not_by_reason -q`. Esperado: FAIL.

- [ ] **Step 3: Implementar.**
  - `validou`: `JOIN tb_lkp_tipos_votos_postagens t ON t.id_tipo_voto = v.tipo_voto_id WHERE t.nome_tipo = 'aprovar'`, mais `AND v.votado_em >= %s` no incremental.
  - `denunciou`: o mesmo JOIN em tipos com `WHERE t.nome_tipo = 'denunciar'` e `LEFT JOIN tb_lkp_motivos_denuncia m ON m.id_motivo_denuncia = v.motivo_denuncia_id`.
  - O Cypher dos dois passos não muda.
  - `CONTAGENS`: contar com o mesmo JOIN e o mesmo filtro.
  - Docstring do módulo: na tabela, trocar "(sem motivo)"/"(com motivo)" por "(tipo aprovar)"/"(tipo denunciar)".

- [ ] **Step 4: Escrever o teste de integração** em `tests/test_graph_etl_postgres.py`:

`test_votes_follow_vote_type_even_with_inconsistent_reason(postgres, pg_url)`. Via `_psql`, inserir dois votos em pares `(postagem, usuário)` que ainda não votaram, escolhidos por `NOT EXISTS` (constraint `votos_postagens_index_7`), com `votado_em = now() - interval '1 day'`:
- A: tipo `denunciar` com `motivo_denuncia_id` NULL;
- B: tipo `aprovar` com `motivo_denuncia_id = 1`.

Depois, `run("full")` com `RecordingNeo4j` e as asserções:
- o par A está em `neo.rows_of("denunciou")` com `motivo is None` e não está em `rows_of("validou")`;
- o par B está em `rows_of("validou")` e não está em `rows_of("denunciou")`;
- `stats["validou"] == _expected(postgres, "VALIDOU")` e o mesmo para `DENUNCIOU`.

- [ ] **Step 5: Rodar.** `pytest tests/test_graph_etl.py tests/test_graph_etl_postgres.py -q`. Esperado: PASS, ou SKIPPED só no arquivo de integração.

- [ ] **Step 6: Suíte inteira verde.**

---

### Task 3: Aresta MORA_NA (Usuario → Torre) e nome dos vizinhos

**Files:**
- Modify: `src/etl/graph_model.py` (`RELATIONSHIP_TYPES` ganha `"MORA_NA"` logo depois de `"MORA_EM"`)
- Modify: `src/etl/populate_graph.py` (passo `mora_em`, `_prune`, `_graph_memberships`, tabela do docstring)
- Modify: `src/etl/validate_graph.py` (`CONTAGENS["MORA_NA"]`)
- Modify: `src/agents/graph_traversal/tools.py` (`conexoes_diretas`)
- Modify: `src/prompts/agents/graph_traversal.py` (lista de relações e exemplo 2)
- Modify: `README.md`, se ele enumerar as relações do grafo (linhas ~314 e ~379)
- Test: `tests/test_graph_etl.py`, `tests/test_graph_etl_postgres.py`, `tests/test_tool_scope.py`

**Interfaces:**
- Consumes: `tb_moradores.torre_id` (Task 1).
- Produces:
  - linhas de `mora_em` com `{usuario_id, condominio_id, torre_id}`, onde `torre_id` pode ser `None`;
  - `_prune()` devolve também `"mora_na_removidas": int`;
  - `_graph_memberships` passa a gerar `(usuario_id, pertence, mora, mora_torre)`;
  - cada item de `conexoes_diretas(...)["conexoes"]` vira `{"label", "id", "nome"}`, com `nome` sempre `None` para `Usuario` e `Postagem`.

- [ ] **Step 1: Escrever os testes que falham.**
  - `tests/test_graph_etl.py::test_mora_em_step_also_maintains_tower_edge`: o `full_sql` e o `incremental_sql` de `mora_em` contêm `m.torre_id`. O `merge` contém `"MERGE (u)-[:MORA_NA]->(t)"` e `"row.torre_id IS NULL OR antiga.id <> row.torre_id"`. `CONTAGENS["MORA_NA"][0]` contém `"torre_id IS NOT NULL"`.
  - `tests/test_tool_scope.py::test_conexoes_diretas_returns_names_but_never_user_names`: fazer monkeypatch de `neo4j_db.execute` para devolver `[{"label_vizinho": "Torre", "id_vizinho": 3, "nome_vizinho": "Torre A"}]`. Sob `tool_scope(MORADOR)`, chamar `conexoes_diretas` com `{"label": "Condominio", "id": 7}`. Asserções: `result["conexoes"] == [{"label": "Torre", "id": 3, "nome": "Torre A"}]` e a query capturada contém `"WHEN vizinho:Usuario OR vizinho:Postagem THEN null"`.
  - `test_graph_vocabulary_matches_agent_whitelist` e `test_validar_covers_every_label_and_relationship` passam a cobrir `MORA_NA` sozinhos (a regex lê o `merge`).

- [ ] **Step 2: Rodar e ver falhar.** `pytest tests/test_graph_etl.py tests/test_tool_scope.py -q`. Esperado: os testes novos falham.

- [ ] **Step 3: Implementar o passo `mora_em`.** O SQL (full e incremental) passa a selecionar também `m.torre_id`. O `merge` fica assim:

```
MATCH (u:Usuario {id: row.usuario_id}), (c:Condominio {id: row.condominio_id})
OPTIONAL MATCH (u)-[old:MORA_EM]->(antigo:Condominio) WHERE antigo <> c
WITH row, u, c, collect(old) AS antigas
FOREACH (x IN antigas | DELETE x)
MERGE (u)-[:MORA_EM]->(c)
WITH row, u
OPTIONAL MATCH (u)-[old_t:MORA_NA]->(antiga:Torre) WHERE row.torre_id IS NULL OR antiga.id <> row.torre_id
WITH row, u, collect(old_t) AS antigas_t
FOREACH (x IN antigas_t | DELETE x)
WITH row, u
OPTIONAL MATCH (t:Torre {id: row.torre_id})
FOREACH (_ IN CASE WHEN t IS NULL THEN [] ELSE [1] END | MERGE (u)-[:MORA_NA]->(t))
```

  - `CONTAGENS["MORA_NA"] = ("SELECT count(*) AS n FROM tb_moradores WHERE torre_id IS NOT NULL", "MATCH (:Usuario)-[r:MORA_NA]->(:Torre) RETURN count(r) AS n")`.
  - `_prune`: montar o conjunto `(usuario_id, torre_id)` de `tb_moradores WHERE torre_id IS NOT NULL`. `_graph_memberships` ganha `OPTIONAL MATCH (u)-[:MORA_NA]->(mt:Torre)` e `collect(DISTINCT mt.id) AS mora_torre`. As sobras são apagadas com `MATCH (:Usuario {id: row.u})-[r:MORA_NA]->(:Torre {id: row.c}) DELETE r` (`log_label="prune_mora_na"`).
  - Docstring do ETL: nova linha na tabela, `(Usuario)-[:MORA_NA]->(Torre)`, origem `tb_moradores.torre_id`, mesmo incremental de `MORA_EM`.

- [ ] **Step 4: Implementar o nome em `conexoes_diretas`.** No `RETURN`, acrescentar `CASE WHEN vizinho:Usuario OR vizinho:Postagem THEN null ELSE coalesce(vizinho.nome, vizinho.titulo) END AS nome_vizinho`. Na saída, `"nome": r.get("nome_vizinho")`.

- [ ] **Step 5: Ajustar o prompt.**
  - Em FONTES DE VERDADE, incluir `MORA_NA` (usuário→torre) junto com `MORA_EM`.
  - Em TOOLS, dizer que `conexoes_diretas` devolve o `nome` de torres, condomínios, categorias, cooperativas e cursos, mas nunca o de pessoas.
  - O exemplo 2 continua com `"torre":"[nome]"`, que agora é verdade.

- [ ] **Step 6: Escrever os testes de integração** em `tests/test_graph_etl_postgres.py`:
  - em `test_full_run_sends_exactly_what_the_validation_expects`, acrescentar `assert sum(r["torre_id"] is not None for r in neo.rows_of("mora_em")) == _expected(postgres, "MORA_NA")`;
  - em `test_prune_computes_orphans_in_python`, a linha fake do `GraphWithLeftovers` ganha `"mora_torre": [424242]`, e entra `assert removidos["mora_na_removidas"] == 1`.

- [ ] **Step 7: Rodar os testes do grafo e a suíte inteira.** `pytest tests/test_graph_etl.py tests/test_graph_etl_postgres.py tests/test_tool_scope.py -q`, depois a suíte inteira. Esperado: verde, com integração PASS ou SKIPPED.

---

### Task 4: Tool `recomendacoes_curso`

**Files:**
- Modify: `src/agents/graph_traversal/tools.py` (nova tool + `TOOLS`)
- Modify: `src/security/tool_scope.py` (`TOOL_ACTIONS["recomendacoes_curso"] = "grafo"`)
- Modify: `src/prompts/agents/graph_traversal.py` (TOOLS, REGRAS, `intencao`)
- Modify: `README.md:71` (o item já está `[x]`; confirmar que o texto descreve a tool como ela ficou)
- Test: `tests/test_tool_scope.py`

**Interfaces:**
- Produces: `recomendacoes_curso(limite: int = 5) -> dict`, com args schema `RecomendacoesCursoArgs(limite: int = Field(default=5, ge=1, le=20))`.
  - Retorno: `{"status": "ok", "recomendacoes": [{"curso_id": int, "titulo": str, "motivo": "reforco" | "vizinhos_aprovados", "score": float}]}`.
  - Sem escopo: `dict(_SEM_ESCOPO)`.
  - Falha do Neo4j: `_grafo_failure(exc)`.

- [ ] **Step 1: Escrever os testes que falham** em `tests/test_tool_scope.py`:
  - `test_recomendacoes_curso_reads_only_the_authenticated_user`: usar `_fake_neo4j`, `tool_scope(MORADOR)` e `ainvoke({"limite": 3})`. Asserções: `params["uid"] == 77`, `params["limite"] == 3`, a query contém `"-[r:RECOMENDADO_PARA]->(u:Usuario {id: $uid})"` e `"usuario_id" not in recomendacoes_curso.args`.
  - `test_recomendacoes_curso_refuses_without_scope`: sem `tool_scope`, a resposta tem `status == "negado"` e `calls == []`.
  - `test_every_analytics_and_grafo_tool_has_a_policy` já cobre o registro em `TOOL_ACTIONS`.

- [ ] **Step 2: Rodar e ver falhar.** `pytest tests/test_tool_scope.py -q`. Esperado: FAIL com ImportError.

- [ ] **Step 3: Implementar.** O escopo vem de `current_scope()`; se for `None`, devolver `_SEM_ESCOPO`. Essa tool não exige condomínio, porque a recomendação de reforço vale sem ele. Query:

```
MATCH (c:Curso)-[r:RECOMENDADO_PARA]->(u:Usuario {id: $uid})
WHERE coalesce(c.ativo, true)
RETURN c.id AS curso_id, c.titulo AS titulo, r.motivo AS motivo, r.score AS score
ORDER BY r.score DESC, c.id
LIMIT $limite
```

- [ ] **Step 4: Ajustar o prompt.**
  - TOOLS: `recomendacoes_curso(limite)`, que devolve os cursos recomendados ao próprio usuário logado, com `motivo` e `score`.
  - REGRAS: `reforco` = "você teve dificuldade nos quizzes desse curso"; `vizinhos_aprovados` = "vizinhos do seu condomínio foram aprovados nele".
  - Lista vazia: usar `intencao = "sem_dados"`.
  - Acrescentar `recomendar_cursos` à lista de `intencao`.
  - Antes, rodar `grep -rn "ranking_conectividade" src`: se aparecer fora do prompt (validação de saída), acrescentar `recomendar_cursos` lá também.

- [ ] **Step 5: Rodar os testes e a suíte inteira.** `pytest tests/test_tool_scope.py -q`, depois a suíte. Esperado: verde.

---

### Task 5: Cortes do ponytail no grafo

**Files:**
- Modify: `src/etl/populate_graph.py` (`Step.params_per_since`, `_run_step`, `_batches`)
- Modify: `src/etl/validate_graph.py` (marcadores `%(min_rep)s`/`%(min_taxa)s` e `substituir`)
- Modify: `src/agents/graph_traversal/tools.py` (`_validar_*`, `_*_PERMITIDOS`, `usuarios_mais_conectados`, `r` sem uso)
- Modify: `src/prompts/agents/graph_traversal.py` (assinatura de `usuarios_mais_conectados`)
- Test: `tests/test_graph_etl.py`, `tests/test_graph_etl_postgres.py`, `tests/test_tool_scope.py`

**Interfaces:**
- Consumes: os passos e as tools como ficaram nas Tasks 2–4.
- Produces:
  - `Step` sem o campo `params_per_since`;
  - `usuarios_mais_conectados(limite: int = 10)`, com o condomínio vindo sempre do escopo;
  - comportamento observável igual, exceto a assinatura dessa tool.

- [ ] **Step 1: Escrever os testes que travam o comportamento.**
  - `tests/test_graph_etl.py::test_incremental_params_match_placeholders`: com `FakePostgres` e `FakeNeo4j(checkpoint=NOW - timedelta(hours=1))`, rodar `run("incremental")`. Para cada `(sql, params)` em `pg.queries` com `params`, verificar `len(params) == sql.count("%s")`.
  - `tests/test_tool_scope.py::test_usuarios_mais_conectados_uses_the_scope_condominio`: com `_fake_neo4j`, `tool_scope(SINDICO)` e `ainvoke({"limite": 5})`, verificar `params["condominio_id"] == 7`.
  - Esse teste substitui `test_usuarios_mais_conectados_refuses_another_condominio`, que deixa de fazer sentido porque o argumento sai.
  - Em `test_grafo_tools_refuse_without_scope`, chamar a tool sem `condominio_id`.

- [ ] **Step 2: Rodar.** `pytest tests/test_graph_etl.py tests/test_tool_scope.py -q`. Esperado: o teste de placeholders PASSA (trava o comportamento atual) e o de `usuarios_mais_conectados` FALHA.

- [ ] **Step 3: Aplicar os cortes.**
  - `Step`: remover o campo `params_per_since` e os 4 `params_per_since=` (`postagens`, `mora_em`, `postagem_relacoes` e o default). Em `_run_step`, usar `params = (since,) * sql.count("%s")`.
  - `_batches`: chamar `self.postgres.iter_batches(sql, params, _BATCH_SIZE)` direto e remover o fallback para `fetch_all`. O `FakePostgres` de `tests/test_graph_etl.py` ganha `iter_batches(self, query, params, size)`: registra em `self.queries` e devolve fatias de `size` (mesmo casamento por `FROM` do `fetch_all`). Ajustar `test_batches_respect_batch_size` se ele fizer monkeypatch de `fetch_all`.
  - `validate_graph.py`: importar `DIFICULDADE_MIN_REPROVACOES` e `DIFICULDADE_MIN_TAXA` no topo do módulo e colocá-los no SQL de `TEM_DIFICULDADE_EM` com f-string. Remover `substituir`, o laço `replace` em `contar` e o `.replace(...)` de `_expected` em `tests/test_graph_etl_postgres.py`.
  - `tools.py`: remover `_LABELS_PERMITIDOS`, `_RELACOES_PERMITIDAS`, `_validar_label` e `_validar_relacao`. Usar `label in NODE_LABELS` e `tipo_relacao is not None and tipo_relacao not in RELATIONSHIP_TYPES` direto, com as mesmas mensagens de erro.
  - Em `test_graph_vocabulary_matches_agent_whitelist`, remover as duas asserções sobre `tools._*_PERMITIDOS`.
  - `usuarios_mais_conectados`: tirar `condominio_id` de `UsuariosMaisConectadosArgs` e da assinatura e usar `escopo["cid"]`.
  - Na query, trocar `[r:CRIOU|VALIDOU]` por `[:CRIOU|VALIDOU]`.
  - No prompt, mudar a assinatura para `usuarios_mais_conectados(limite)`.

- [ ] **Step 4: Rodar os testes e a suíte inteira.** Esperado: verde. Relatar o saldo de linhas com `git diff --stat` (alvo: cerca de -30 linhas de código de produção nesta task).

---

## Fora do plano (decidido)

- `run_forever` / `--loop-seconds`: fica, porque é o que o `docker compose --profile etl` usa.
- `GraphPopulator(now=)`: fica, é a costura dos testes.
- `_postagem` (isoformat de `capturada_em`): fica. Trocar por DateTime mudaria o tipo da propriedade nos nós que já existem no grafo.
- Linhas em branco do `src/database/neo4j.py`: é só formatação, fica.
- Incremental de `tb_moradores`, nome/perfil de usuário e troca do responsável da cooperativa: continuam sendo atualizados só no full diário (documentado no ETL).
