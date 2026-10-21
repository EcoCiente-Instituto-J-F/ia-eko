# EcoCiente IA

[English](README-en.md) · **Português (Brasil)**

![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.141-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1.2-1C3C3C?style=for-the-badge)
![Neo4j](https://img.shields.io/badge/Neo4j-5-4581C3?style=for-the-badge&logo=neo4j&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-14+-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)
![MongoDB](https://img.shields.io/badge/MongoDB-async-47A248?style=for-the-badge&logo=mongodb&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![GitHub repo size](https://img.shields.io/github/repo-size/EcoCiente-Instituto-J-F/ia-eko?style=for-the-badge)
![GitHub last commit](https://img.shields.io/github/last-commit/EcoCiente-Instituto-J-F/ia-eko?style=for-the-badge)
![License](https://img.shields.io/badge/license-MIT-green?style=for-the-badge)

> API multiagente do EcoCiente: FastAPI + LangChain/LangGraph orquestrando especialistas (FAQ, educação ambiental, coletas, analytics e grafo de relacionamentos) com RAG, memória conversacional persistente, guardrails de entrada/saída, juiz anti-alucinação e integrações MCP/A2A.

## Sobre o projeto

O EcoCiente é uma plataforma de gestão de reciclagem para condomínios que conecta moradores,
síndicos e cooperativas de coleta. Este repositório é o backend de IA: um chatbot multiagente
que responde de forma diferente conforme o perfil autenticado (`usuario_comum`,
`sindico_residencial`, `sindico_comercial`, `morador_residencial`, `usuario_comercial`,
`cooperativa`), cada um com seu próprio recorte de permissões.

Uma mensagem do usuário passa por um pipeline determinístico em grafo (LangGraph): validação
de entrada, recuperação de memória, roteamento para o especialista correto, validação de saída
por um juiz e, se reprovada, uma rodada de correção — tudo isso instrumentado com Prometheus e
exposto via FastAPI.

| Perfil | Pode acessar |
| --- | --- |
| `usuario_comum` | `faq`, `educacional` |
| `sindico_residencial` / `sindico_comercial` | `faq`, `educacional`, `analytics`, `coletas`, `grafo` |
| `morador_residencial` | `faq`, `educacional`, `analytics` (próprios dados), `grafo` |
| `usuario_comercial` | `faq`, `educacional`, `analytics` (próprios dados), `coletas` |
| `cooperativa` | `faq`, `coletas` |

As regras completas de autorização (perfil × ação, não só perfil × agente) estão em
`src/security/policies.py`.

### Estado atual

O projeto está em desenvolvimento ativo:

- [x] Pipeline LangGraph com guardrails de entrada/saída, memória e juiz de correção
- [x] Especialistas `faq` e `educacional` via RAG (busca vetorial em memória + base de conhecimento local + fonte externa)
- [x] Especialista `analytics` sobre PostgreSQL (consultas somente leitura) e ranking via Redis
- [x] Especialista `coletas` integrado à API externa de calendário via MCP
- [x] Especialista `grafo` sobre Neo4j para caminhos, conexões e influência entre entidades
- [x] Memória conversacional persistida no MongoDB com compactação automática
- [x] Autorização por perfil e por ação sensível (`src/security/policies.py`)
- [x] Escopo das tools por usuário: o LLM não escolhe de quem são os dados que uma tool lê (`src/security/tool_scope.py`)
- [x] Juiz de saída confere o rascunho contra as evidências que o especialista realmente consultou (trechos do RAG ou retorno das tools)
- [x] Observabilidade com Prometheus (`/metrics`), dashboard Grafana e healthcheck agregado (`/health`)
- [x] Custo, custo por resolução e ROI projetados para 100 e 1000 usuários semanais (`GET /api/v1/costs`)
- [x] Tracing de LLM com Langfuse (self-hosted) ou LangSmith, com dados pessoais mascarados
- [x] Cota diária por perfil, limite por minuto e lock da sessão no Redis entre réplicas
- [x] Endpoint SSE `/api/v1/chat/stream` com progresso do pipeline
- [x] FAQ por busca vetorial no Qdrant (`FAQ_BACKEND=qdrant`), respondendo com texto curado e sem chamar LLM
- [x] ETL do grafo PostgreSQL → Neo4j incremental e agendado, validado em PostgreSQL 16 com até 1,8 mi de votos
- [x] Evals de roteamento (`python -m evals.routing.run`) com acurácia, F1 por rota e matriz de confusão
- [x] Servidor A2A e servidor MCP para integração com outros agentes
- [x] CI no GitHub Actions rodando a suíte `pytest`
- [x] Rodar os evals com o modelo de produção e registrar a acurácia de referência
- [x] Rodar `python -m src.etl.populate_graph --validar` no Aura e registrar os tempos reais
- [x] Tool do agente `grafo` para consultar recomendações de curso
- [x] Memória de longo prazo por usuário entre conversas (`GET`/`DELETE /api/v1/memory`)
- [x] Cliente A2A: o agente `educacional` consulta um agente externo quando `A2A_REMOTE_AGENT_URL` está configurado

## Pré-requisitos

- Python `3.11` ou superior
- PostgreSQL `14+`, MongoDB e Redis (podem subir via Docker)
- Neo4j `5+` (opcional — só é necessário para o especialista `grafo`; a API sobe sem ele)
- Qdrant (opcional — só com `FAQ_BACKEND=qdrant`)
- Ollama rodando localmente (provider padrão de LLM/embeddings) **ou** uma chave Groq/Gemini/DeepSeek
- Docker e Docker Compose, se for usar os bancos em contêiner

## Instalando

Clone o repositório:

```bash
git clone https://github.com/EcoCiente-Instituto-J-F/ia-eko.git
cd ia-eko
```

Crie e ative o ambiente virtual:

```bash
python -m venv .venv
source .venv/bin/activate      # Linux/macOS
.venv\Scripts\activate         # Windows
```

Instale as dependências:

```bash
pip install -r requirements.txt
```

> Para usar Groq, Gemini ou DeepSeek como provider de LLM, instale `requirements-optional.txt`
> no lugar (ele já inclui o `requirements.txt` base).

Configure as variáveis de ambiente:

```bash
cp .env.example .env           # Windows: copy .env.example .env
```

<details>
<summary>Principais variáveis do <code>.env</code></summary>

| Variável | Padrão | Descrição |
| --- | --- | --- |
| `APP_ENV` | `development` | `development`, `test` ou `production` |
| `LLM_PROVIDER` | `ollama` | `ollama`, `groq`, `gemini`, `deepseek` ou `mock` (sem custo de API, usado nos testes) |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | `http://localhost:11434` / `qwen3:4b` | LLM local via Ollama |
| `POSTGRES_URL` | — | Fonte de verdade dos dados analíticos (consultada só para leitura) |
| `MONGODB_URI` / `MONGODB_DATABASE` | `mongodb://localhost:27017` / `ecociente` | Memória conversacional das sessões |
| `REDIS_URL` | `redis://localhost:6379/0` | Ranking, ponteiro de sessão, cotas de uso e lock da sessão |
| `NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD` / `NEO4J_DATABASE` | `bolt://localhost:7687` / `neo4j` | Camada de relacionamentos do especialista `grafo` (opcional) |
| `KNOWLEDGE_BASE_PATH` | `data/FAQ_KNOWLEDGE_BASE.md` | Base RAG dos especialistas `faq` e `educacional` |
| `CALENDAR_API_BASE_URL` | `http://localhost:9800` | API externa de coletas, consumida via MCP |
| `AUTH_API_URL` | — | API externa que valida o token e devolve os dados do usuário |
| `JUDGE_MAX_CORRECTIONS` | `1` | Quantas vezes o juiz de saída pode pedir correção |
| `MEMORY_MAX_MESSAGES` / `MEMORY_KEEP_RECENT_MESSAGES` | `20` / `6` | Quando e quanto da memória é compactada |
| `LLM_PRICE_INPUT_PER_1M_BRL` / `LLM_PRICE_OUTPUT_PER_1M_BRL` | `0` | Preço do provedor por 1 milhão de tokens (R$); `0` = Ollama |

Veja `.env.example` para a lista completa (premissas de custo, tracing, testes de integração, A2A…).

</details>

> [!NOTE]
> O `docker-compose.yml` sobe a `api`, o Prometheus e o Grafana (e o `graph-etl` no profile
> `etl`). Os bancos (Postgres, MongoDB, Redis, Neo4j, Qdrant) **não** estão nele: precisam estar
> acessíveis nos endereços do `.env`. Para desenvolver sem nenhum deles, use
> `STORAGE_MODE=memory` e `LLM_PROVIDER=mock`.

## Usando

Com as dependências no ar e o `.env` preenchido:

```bash
python -m uvicorn src.api.main:app --reload
```

A API sobe em `http://127.0.0.1:8000`. Endpoints principais:

| Método | Rota | Descrição |
| --- | --- | --- |
| `POST` | `/api/v1/chat` | Envia uma mensagem e recebe a resposta do especialista roteado |
| `POST` | `/api/v1/chat/stream` | Mesmo pipeline, com progresso por etapa via SSE (`session` → `progress` → `answer` → `done`) |
| `GET` | `/api/v1/chat/quota` | Quantas respostas o usuário ainda tem hoje |
| `GET` | `/api/v1/agents` | Lista os especialistas disponíveis |
| `POST` / `GET` / `DELETE` | `/api/v1/sessions`, `/api/v1/sessions/{session_id}` | Cria, consulta e encerra uma sessão de conversa |
| `GET` / `DELETE` | `/api/v1/memory` | O que o assistente lembra do usuário entre conversas; pode ser apagado a qualquer momento |
| `GET` | `/api/v1/rankings/moradores`, `/torres`, `/me` | Ranking de reciclagem (conforme perfil) e a posição do próprio usuário |
| `GET` | `/api/v1/costs` | Custo, custo por resolução e ROI projetados para 100 e 1000 usuários/semana |
| `GET` | `/health` | Healthcheck agregado (LLM, Postgres, Mongo/Redis, RAG, Neo4j, Qdrant, calendário) |
| `GET` | `/metrics` | Métricas Prometheus |
| `GET` | `/docs` | Swagger UI (OpenAPI) |

Exemplo de chamada (em `APP_ENV=test` com `ALLOW_TEST_IDENTITY_HEADERS=true`, sem precisar de
token real):

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-Usuario-Id: 42" \
  -H "X-Perfil: sindico_residencial" \
  -H "X-Condominio-Id: 7" \
  -d '{
        "usuario_id": 42,
        "session_id": null,
        "mensagem": "Quais moradores mais influenciam a reciclagem do condomínio?"
      }'
```

Resposta (resumida):

```json
{
  "session_id": "…",
  "agent": "grafo",
  "answer": "…",
  "agents_called": ["guardrail_entrada", "memoria", "verificar_compactacao", "orquestrador", "grafo", "juiz_saida", "guardrail_saida"],
  "sources": [],
  "judge": { "aprovado": true, "motivo": "aprovado", "necessita_correcao": false, "categoria": "aprovado" },
  "usage": { "input_tokens": 1830, "output_tokens": 212, "total_tokens": 2042 },
  "quota": { "limite_diario": 100, "usadas_hoje": 3, "restantes_hoje": 97, "renova_em": "2026-09-28T00:00:00-03:00" }
}
```

Em produção, o `Authorization: Bearer <token>` é obrigatório e a identidade vem da API de
autenticação (`AUTH_API_URL`) — os headers `X-*` acima só funcionam com `APP_ENV=test`.

### Limites de uso

Checados antes de qualquer chamada de LLM (`src/services/quota_service.py`). Estourou → HTTP
`429` com `Retry-After` e `detail.codigo` = `limite_daily`, `limite_rate` ou `limite_session`.

| Perfil | Respostas/dia | Observação |
| --- | --- | --- |
| `usuario_comum` | 20 | Sessão encerrada em 10 perguntas: o resumo da sessão nunca é acionado; a memória de longo prazo continua sendo atualizada em segundo plano após cada resposta |
| `morador_residencial` | 40 | |
| `usuario_comercial` | 50 | |
| `cooperativa` | 80 | Uso operacional (agenda de coletas) |
| `sindico_residencial` / `sindico_comercial` | 100 | Analytics e gestão do condomínio |

Além disso, qualquer perfil tem no máximo 10 mensagens por minuto. A cota vira à meia-noite de
`QUOTA_TIMEZONE` e não é consumida quando a resposta falha por erro do servidor. Tudo é
configurável (`QUOTA_*`, `RATE_LIMIT_PER_MINUTE`).

Mensagens da mesma sessão são processadas uma por vez, inclusive com várias réplicas da API: o
lock fica no Redis (`session:lock:<id>`, `SET NX PX`, renovado enquanto a resposta é gerada e
liberado sozinho pelo TTL se o pod morrer). Uma segunda mensagem espera a primeira terminar por
até `SESSION_LOCK_WAIT_SECONDS` (45 s); passou disso, HTTP `409` com `detail.codigo` =
`sessao_ocupada` e a cota devolvida. Sem Redis (`STORAGE_MODE=memory`), o lock é só do processo.

### Observabilidade

```bash
# API + Prometheus (9090) + Grafana (3001, dashboard "EcoCiente IA" já provisionado)
docker compose up -d

# Langfuse self-hosted (3000), com projeto e chaves de dev já criados
docker compose -f docker-compose.langfuse.yml -p langfuse up -d
```

Para ligar o tracing, no `.env`:

```env
TRACING_PROVIDER=langfuse
LANGFUSE_PUBLIC_KEY=pk-lf-ecociente-local
LANGFUSE_SECRET_KEY=sk-lf-ecociente-local
LANGFUSE_HOST=http://localhost:3000
```

Cada agente vira um span agrupado por sessão e usuário. Antes de sair da API, o conteúdo passa
por `mask_pii` (CPF, CNPJ, e-mail, telefone, CEP, JWT e chaves como `token`/`senha`).
`TRACING_PROVIDER=langsmith` com `LANGSMITH_API_KEY` também funciona, com o mesmo mascaramento.

Custo e ROI: cada resposta do `/chat` conta como "resolvida" quando o juiz de saída aprovou a
resposta final (bloqueio de guardrail ou de autorização, e resposta reprovada após as correções,
não contam). `GET /api/v1/costs` projeta custo, custo por resolução e ROI para 100 e 1000
usuários/semana a partir das médias medidas e das premissas do `.env`; sem dados, devolve
`null` em vez de números inventados. Método, fórmulas e variáveis em
[`docs/SRE_E_CUSTOS.md`](docs/SRE_E_CUSTOS.md).

| Ferramenta | Responde |
| --- | --- |
| Prometheus/Grafana | Quanto e quão rápido: latência por agente, taxa de reprovação do juiz, tokens/min, recusas por limite, índice de erros, custo por resolução |
| Langfuse/LangSmith | O que aconteceu nesta conversa: prompt, resposta, tokens e decisão de cada agente |

### FAQ via Qdrant (sem LLM)

Com `FAQ_BACKEND=qdrant`, o especialista `faq` não chama LLM:

1. A pergunta vira embedding localmente (`fastembed`, `intfloat/multilingual-e5-large`,
   prefixo `query:`). Nenhuma chamada de API é feita nessa etapa.
2. A coleção `QDRANT_COLLECTION` é consultada. O payload de cada ponto tem `faq_id`, `titulo`,
   `intencao`, `perfis_relacionados`, `resposta_canonica`, `palavras_chave` e `tipo`.
3. Vale o primeiro resultado com score acima de `QDRANT_MIN_SCORE` cujos `perfis_relacionados`
   incluam o perfil do usuário (lista vazia vale para todos); a `resposta_canonica` dele é a
   resposta final. Por ser texto curado, o juiz de saída também não chama LLM.
4. Se nenhum resultado passar: com `FAQ_LLM_FALLBACK=false` (padrão), a resposta diz que a base
   não tem essa pergunta; com `true`, cai no RAG + LLM.

```env
FAQ_BACKEND=qdrant
QDRANT_URL=https://<cluster>.cloud.qdrant.io
QDRANT_API_KEY=...
QDRANT_COLLECTION=faq
QDRANT_MIN_SCORE=0.80
```

Carga da coleção a partir da seção "Perguntas Frequentes Canônicas" de
`data/FAQ_KNOWLEDGE_BASE.md` (64 FAQs `FAQ-NNN`):

```bash
python -m src.etl.populate_qdrant --dry-run      # valida o arquivo, não toca no Qdrant
python -m src.etl.populate_qdrant                # cria/atualiza a coleção
python -m src.etl.populate_qdrant --testar "como faço login?" "posso apagar minha conta?"
```

A carga é idempotente (id do ponto derivado do `faq_id`), só gera embedding do que mudou (hash
no payload) e remove da coleção os FAQs que saíram do arquivo. Rode de novo sempre que a base
mudar.

> [!WARNING]
> O modelo de embedding (`QDRANT_EMBEDDING_MODEL`, ~2,2 GB, baixado no primeiro uso) tem que ser
> o mesmo na carga e na API. A carga grava o modelo em cada ponto e o `/health` acusa `qdrant:
> error` se divergir; trocar de modelo exige `--recriar`. Calibre `QDRANT_MIN_SCORE` com
> `--testar` e perguntas reais: scores do e5 costumam ficar entre 0,75 e 0,90.

### Carga do grafo

```bash
python -m src.etl.populate_graph --mode full            # tudo
python -m src.etl.populate_graph --mode full --prune    # tudo + remove do grafo o que não existe mais
python -m src.etl.populate_graph --mode incremental     # só o que mudou desde a última carga
python -m src.etl.populate_graph --validar --relatorio etl.json  # confere Postgres × grafo, com tempos
docker compose --profile etl up -d graph-etl            # loop: incremental 15 min, full 24 h
kubectl apply -f k8s/graph-etl-cronjobs.yaml            # CronJobs equivalentes
```

O incremental usa as datas do schema e a auditoria de `tb_rel_usuarios_condominios`. As regras
de `TEM_DIFICULDADE_EM` e `RECOMENDADO_PARA` estão no topo de `src/etl/populate_graph.py`.
Medido em PostgreSQL 16 com o schema do projeto e 200 mil usuários, 600 mil postagens e 1,8 mi
de votos (só a leitura e o preparo dos lotes): full em ~21 s com ~125 MB de memória;
incremental com atividade de 15 min em ~1 s.

### Docker

```bash
docker build -t ecociente-ia .
docker run --env-file .env -p 8000:8000 ecociente-ia
```

### Kubernetes

Manifests de referência em `k8s/` (namespace, ConfigMap, Secret de exemplo, Deployment, Service
e os CronJobs da carga do grafo, com `app.kubernetes.io/name: ia-eko`). O deploy da AWS é
mantido pela equipe de infraestrutura em
[`devops-infra-ecociente`](https://github.com/EcoCiente-Instituto-J-F/devops-infra-ecociente)
(Helm).

## Como funciona

### Pipeline de uma mensagem

```mermaid
flowchart TD
    A[POST /api/v1/chat] --> B[guardrail_entrada]
    B -- bloqueado --> Z[guardrail_saida]
    B -- ok --> C[memoria]
    C --> D{precisa compactar?}
    D -- sim --> E[resumir_memoria]
    D -- não --> F[orquestrador]
    E --> F
    F -- autorizado --> G{roteia para}
    F -- não autorizado --> Z
    G --> H[faq / educacional<br/>RAG]
    G --> I[analytics<br/>PostgreSQL + Redis]
    G --> J[coletas<br/>API externa via MCP]
    G --> K[grafo<br/>Neo4j]
    H & I & J & K --> L[juiz_saida]
    L -- aprovado --> Z
    L -- reprovado, tentativas restantes --> M[correcao] --> L
    L -- reprovado, sem tentativas --> Z
    Z --> N[Resposta ao usuário]
```

O juiz de saída recebe o rascunho junto com as evidências que o especialista realmente
consultou — trechos recuperados pelo RAG ou o retorno das tools de PostgreSQL/Neo4j — e reprova
ou censura o que não estiver nelas (exceto o FAQ canônico do Qdrant e o especialista `coletas`,
que não passam pelo juiz LLM).

### Especialistas

| Agente | Fonte de dados | Quando responde |
| --- | --- | --- |
| `faq` | RAG sobre `data/FAQ_KNOWLEDGE_BASE.md`, ou Qdrant sem LLM (`FAQ_BACKEND=qdrant`) | Regras, políticas e limites do assistente |
| `educacional` | RAG sobre a mesma base + fonte externa (SINIR) | Separação de resíduos, compostagem, sustentabilidade |
| `analytics` | PostgreSQL (leitura) + Redis (ranking) | Pontos, desempenho, métricas e comparações — sempre que a resposta é um número ou série |
| `coletas` | API externa de calendário via MCP | Agendamento, recorrência e confirmação de coleta |
| `grafo` | Neo4j | Caminho, conexão ou influência entre entidades — quando a resposta é a estrutura da relação, não uma métrica |

O agente `grafo` existe porque perguntas como *"quais moradores mais influenciam a reciclagem
do condomínio?"* não são respondidas por uma agregação SQL — elas pedem a topologia da rede de
relacionamentos (`Usuario`, `Condominio`, `Torre`, `Cooperativa`, `CategoriaResiduo`, `Curso`,
`Postagem` e relações como `MORA_EM`, `PERTENCE_A`, `CRIOU`, `VALIDOU`, `DENUNCIOU`,
`TEM_DIFICULDADE_EM` e `RECOMENDADO_PARA`). O vocabulário fica em um lugar só
(`src/etl/graph_model.py`), usado pelo ETL e pela whitelist das tools; as queries Cypher
expostas ao LLM estão em `src/agents/graph_traversal/tools.py`.

### Segurança

- **Autenticação:** token Bearer validado pela API externa de autenticação; os headers de teste
  `X-*` só funcionam com `APP_ENV=test` e `ALLOW_TEST_IDENTITY_HEADERS=true`.
- **Autorização:** regras perfil × agente e perfil × ação em `src/security/policies.py`,
  checadas antes do roteamento.
- **Escopo das tools:** o `ToolScopeMiddleware` intercepta toda chamada de tool. O
  `condominio_id` é sempre o do usuário e o `usuario_id` é sempre o próprio (só o síndico pode
  pedir o agregado do condomínio). O Cypher do grafo só percorre nós do condomínio do usuário, e
  quem não é síndico não vê nós de outros moradores.
- **Guardrails e juízes:** checagens determinísticas, um juiz LLM de entrada (prompt injection,
  dado de terceiro, fraude) e um juiz LLM de saída baseado nas evidências recuperadas.
- **Privacidade:** dados pessoais são mascarados antes de os traces saírem da API; segredos são
  removidos das respostas.

Para reportar uma vulnerabilidade, veja [SECURITY.md](SECURITY.md).

### Arquitetura

- `api/` — rotas FastAPI e injeção de dependência
- `agents/` — grafo LangGraph e os especialistas (`analytics/`, `coleta/`, `graph_traversal/`)
- `prompts/` — todo o conteúdo de sistema dos agentes, fora da infraestrutura
- `services/` — casos de uso reutilizáveis (sessão, ranking, RAG, health, memória, cotas)
- `database/` — lifecycle dos clientes/pools (PostgreSQL, MongoDB, Redis, Neo4j)
- `integrations/` — MCP, A2A, autenticação e a API externa de calendário
- `security/` — autenticação, autorização por perfil/ação, escopo das tools e guardrails
- `observability/` — métricas Prometheus, middleware, tracing e custo

Decisões arquiteturais em `docs/ARQUITETURA.md`.

## Testes

```bash
LLM_PROVIDER=mock EMBEDDING_PROVIDER=mock STORAGE_MODE=memory ENABLE_EXTERNAL_SOURCE=false pytest
```

A suíte roda em `LLM_PROVIDER=mock` (sem custo de API — respostas determinísticas simulam cada
agente). Alguns testes sobem serviços reais sozinhos se os binários existirem na máquina e são
pulados se não existirem: `redis-server` (lock da sessão entre réplicas) e PostgreSQL
(`initdb`/`pg_ctl`, ETL do grafo sobre o schema real); `TEST_REDIS_URL` e `TEST_POSTGRES_URL`
apontam para servidores já existentes. Os testes de integração em `tests/integration/` exigem
`RUN_INTEGRATION_TESTS=true` com `APP_ENV=test`.

### Evals de roteamento

`pytest` garante que o código funciona; os evals medem se o **modelo** roteia bem. São 58
perguntas rotuladas (`evals/routing/dataset.jsonl`, fácil/média/difícil) passadas pelo mesmo
prompt e parser da produção:

```bash
python -m evals.routing.run                       # usa o LLM_PROVIDER do .env
python -m evals.routing.run --rpm 10 --concurrency 1   # Gemini free: respeita o limite por minuto
python -m evals.routing.run --min-accuracy 0.85   # exit 1 abaixo do mínimo (para CI)
```

Saída: acurácia, precisão/recall/F1 por rota, acurácia por dificuldade, matriz de confusão e a
lista de erros, gravadas em `evals/reports/`. Falha de API (429, timeout) é tentada de novo com
espera crescente e, se persistir, aparece como `erro_api`, separada de erro de roteamento. Em
`LLM_PROVIDER=mock` a heurística de palavras-chave acerta ~57% — serve só para validar o harness.

## Estrutura do projeto

```
ia-eko/
├── src/
│   ├── api/                  # rotas, schemas, dependências FastAPI
│   ├── agents/
│   │   ├── graph.py          # StateGraph (LangGraph) — o pipeline inteiro
│   │   ├── factory.py        # constrói os agentes LangChain / modo mock
│   │   ├── analytics/        # tools PostgreSQL/Redis do especialista analytics
│   │   ├── graph_traversal/  # tools Neo4j do especialista grafo
│   │   └── coleta/           # integração MCP com a API de calendário
│   ├── prompts/              # prompts de sistema de cada agente
│   ├── services/             # sessão, ranking, RAG, memória, health, cotas
│   ├── database/             # clientes/pools: postgres, mongodb, redis, neo4j
│   ├── etl/                  # cargas PostgreSQL → Neo4j e FAQ → Qdrant, vocabulário do grafo
│   ├── observability/        # métricas Prometheus, middleware, tracing, custo
│   ├── integrations/         # MCP, A2A, auth, calendário
│   └── security/             # autenticação, policies, escopo das tools, guardrails
├── evals/routing/            # dataset rotulado e avaliação do roteamento
├── observability/            # config do Prometheus (alertas) e Grafana (dashboard)
├── tests/                    # pytest (unitário, integration/ e fixtures/ com o seed do ETL)
├── docs/                     # arquitetura, SRE/custos e referências técnicas
├── sql/                      # schema PostgreSQL (DDL)
├── k8s/                      # manifests Kubernetes
├── scripts/                  # pr-bot (descrição de PR) e benchmark da API
├── Dockerfile
├── docker-compose.yml        # API + Prometheus + Grafana (+ graph-etl no profile "etl")
├── docker-compose.langfuse.yml  # Langfuse self-hosted
├── requirements.txt
└── .env.example
```

## Colaboradores

Agradecemos às seguintes pessoas que contribuíram para este projeto:

<table>
  <tr>
    <td align="center">
      <a href="https://github.com/shinitihm" title="Perfil no GitHub">
        <img src="https://github.com/shinitihm.png" width="100px;" alt="Foto de shinitihm no GitHub"/><br>
        <sub>
          <b>shinitihm</b>
        </sub>
      </a>
    </td>
  </tr>
</table>

## Contribuindo

Contribuições são bem-vindas — leia o [CONTRIBUTING.md](CONTRIBUTING.md) (em inglês) para o
setup, os comandos de teste e o fluxo de pull request. Todos que participam devem seguir o
[Código de Conduta](CODE_OF_CONDUCT.md).

## Licença

Este projeto está sob a licença MIT. Veja o arquivo [LICENSE](LICENSE) para mais detalhes.
