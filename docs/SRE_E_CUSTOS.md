# SRE, observabilidade e custos

## Métricas instrumentadas

O endpoint `/metrics` exporta Prometheus:

- `ecociente_http_requests_total{method,route,status}`
- `ecociente_http_request_errors_total{method,route}`
- `ecociente_http_request_duration_seconds{method,route}`
- `ecociente_agent_duration_seconds{agent}`
- `ecociente_rag_duration_seconds{operation}`
- `ecociente_tool_duration_seconds{tool}`
- `ecociente_db_duration_seconds{backend,operation}`
- `ecociente_llm_duration_seconds{provider,agent}`
- `ecociente_agents_called_per_chat`
- `ecociente_guardrail_blocked_total{reason}`
- `ecociente_judge_rejected_total{reason}`

Os logs JSON incluem `request_id`, rota, status e duração. Logs do grafo não registram senhas, chaves, tokens, CPF completo nem corpo HTTP bruto.

## Latência

São medidos separadamente HTTP total, agentes/nós, RAG, ferramenta analytics, MongoDB, Redis e LLM. As fases do grafo são observáveis pelos rótulos `guardrail_entrada`, `memoria`, `verificar_compactacao`, `resumir_memoria` (quando necessário), `orquestrador`, especialista, `juiz_saida`, `correcao` e `guardrail_saida`.

| Indicador | Valor atual |
|---|---|
| média `/chat` | **VALOR A MEDIR** |
| mediana | **VALOR A MEDIR** |
| p95 | **VALOR A MEDIR** |
| p99 | **VALOR A MEDIR** |
| latência interagentes | **VALOR A MEDIR** via histogramas por nó + `request_id` |
| throughput | **VALOR A MEDIR** |

Use `scripts/benchmark_api.py` em `mock`/Ollama local. Não há números inventados.

## Error rate

```text
error_rate = requisições_com_erro / requisições_totais
```

Para HTTP, derive dos contadores Prometheus. Defina claramente no dashboard se “erro” significa `5xx` apenas ou inclui `4xx` de cliente; a métrica `ecociente_http_request_errors_total` foi desenhada para falhas de servidor/exceções.

## Custos — provider padrão Ollama

`LLM_PROVIDER=ollama` implica **custo de API generativa = R$ 0**. Isso não significa custo total zero. Infraestrutura, energia, bancos, armazenamento, backup e observabilidade continuam existindo.

Defina:

- `q` = solicitações médias por usuário/semana (**VALOR A MEDIR**)
- `I_n` = custo semanal de infraestrutura necessária para `n` usuários (**VALOR A MEDIR**)
- `E_n` = energia semanal (**VALOR A MEDIR**)
- `B_n` = bancos/backup (**VALOR A MEDIR**)
- `S_n` = armazenamento/observabilidade (**VALOR A MEDIR**)
- `A_n` = APIs opcionais pagas; no cenário Ollama puro, `A_n = R$ 0`

### 100 usuários semanais

```text
solicitacoes_100 = 100 * q
custo_total_100 = I_100 + E_100 + B_100 + S_100 + A_100
A_100 = R$ 0  (Ollama puro)
custo_por_solicitacao_100 = custo_total_100 / solicitacoes_100
```

Todos os valores monetários de infraestrutura: **VALOR A MEDIR / COTAR**.

### 1000 usuários semanais

```text
solicitacoes_1000 = 1000 * q
custo_total_1000 = I_1000 + E_1000 + B_1000 + S_1000 + A_1000
A_1000 = R$ 0  (Ollama puro)
custo_por_solicitacao_1000 = custo_total_1000 / solicitacoes_1000
```

Não se assume que `I_1000 = 10 * I_100`; capacidade de GPU/CPU/RAM e concorrência devem ser medidas por benchmark.

## Custo por resolução

Defina `r` como quantidade de solicitações resolvidas sem transferência/retrabalho no período:

```text
custo_por_resolucao = custo_total / r
```

`r`: **VALOR A MEDIR**. Uma resolução deve ter critério operacional definido pelo grupo (por exemplo, resposta aprovada pelo juiz e sem reabertura da mesma intenção em X minutos).

## ROI

Não há receita/benefício monetário informado, portanto não é inventado:

```text
ROI = (beneficio_estimado - custo_total) / custo_total
```

- `beneficio_estimado`: **VALOR A FORNECER/MEDIR PELO GRUPO**.
- `custo_total`: obtido pelas fórmulas acima.
- ROI atual: **VALOR A MEDIR**.

## Benchmark

```bash
APP_ENV=test ALLOW_TEST_IDENTITY_HEADERS=true LLM_PROVIDER=mock EMBEDDING_PROVIDER=mock \
STORAGE_MODE=memory ENABLE_EXTERNAL_SOURCE=false python -m uvicorn src.api.main:app
python scripts/benchmark_api.py --requests 100 --concurrency 10
```

O benchmark calcula média, mediana, p95, p99, taxa de erro e throughput.
