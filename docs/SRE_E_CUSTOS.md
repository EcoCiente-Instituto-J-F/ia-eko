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
- `ecociente_chat_cost_brl_total` (custo estimado de API, em R$)
- `ecociente_chats_total{resolved="true"|"false"}`

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

No Grafana, o painel "Índice de erros HTTP (5m)" usa:

```promql
sum(rate(ecociente_http_request_errors_total[5m])) / sum(rate(ecociente_http_requests_total[5m]))
```

## Custos

`LLM_PROVIDER=ollama` implica **custo de API generativa = R$ 0**. Isso não significa custo total zero: infraestrutura, energia, bancos, armazenamento, backup e observabilidade continuam existindo. Com Groq, Gemini ou DeepSeek, o custo de API passa a ser medido pelos tokens de cada resposta.

### O que o sistema mede

A cada resposta do `/chat`, o custo estimado é:

```text
custo_resposta = (tokens_entrada * LLM_PRICE_INPUT_PER_1M_BRL
                + tokens_saida   * LLM_PRICE_OUTPUT_PER_1M_BRL) / 1_000_000
```

e é somado em `ecociente_chat_cost_brl_total`. Os preços são sempre os do provedor ativo, em R$ por 1 milhão de tokens.

### Definição de resolução

Uma solicitação é **resolvida** quando a resposta final foi **aprovada pelo juiz de saída**. Não contam como resolvida: bloqueio por guardrail, bloqueio por autorização e resposta reprovada depois das correções. Cada resposta incrementa `ecociente_chats_total{resolved="true"}` ou `{resolved="false"}`.

Respostas do FAQ canônico (Qdrant, `FAQ_BACKEND=qdrant`) e do especialista `coletas` são aprovadas de forma determinística quando não vazias (sem juiz LLM); por isso as mensagens de fallback/indisponibilidade desses caminhos também contam como resolvidas. Limitação conhecida.

### Variáveis de ambiente

| Variável | Padrão | Significado |
|---|---|---|
| `LLM_PRICE_INPUT_PER_1M_BRL` | `0` | R$ por 1M de tokens de entrada do provedor ativo. **Informar** para Groq/Gemini/DeepSeek; `0` = Ollama |
| `LLM_PRICE_OUTPUT_PER_1M_BRL` | `0` | R$ por 1M de tokens de saída. **Informar** (idem) |
| `COST_REQUESTS_PER_USER_WEEK` | `5` | `q`: solicitações por usuário/semana. **Medir** com uso real; 5 é só premissa |
| `COST_INFRA_WEEKLY_BRL_100` | `0` | Infra semanal (servidor/GPU, bancos, energia, backup) para 100 usuários. **Informar/medir** |
| `COST_INFRA_WEEKLY_BRL_1000` | `0` | Idem para 1000 usuários. Não se assume `10 * ` o de 100; capacidade de GPU/CPU/RAM e concorrência devem ser medidas por benchmark |
| `COST_BENEFIT_PER_RESOLUTION_BRL` | `0` | Benefício de cada resolução (ex.: atendimento humano evitado). **Informar pelo grupo** |

Valores negativos impedem a aplicação de subir. Enquanto estiverem em `0`, o que depender deles (infra, benefício, ROI) reflete esse zero, não uma medição.

### Fórmulas

`CostLedger.project(settings, users)` calcula, para `users` em 100 e 1000, a partir do acumulado medido (`solicitacoes_medidas`, `custo_medido`, `resolvidas`):

```text
solicitacoes_semana       = users * q
custo_api                 = solicitacoes_semana * (custo_medido / solicitacoes_medidas)
custo_total               = custo_api + infra_do_cenario
custo_por_solicitacao     = custo_total / solicitacoes_semana
resolucoes_semana         = solicitacoes_semana * (resolvidas / solicitacoes_medidas)
custo_por_resolucao       = custo_total / resolucoes_semana
beneficio                 = resolucoes_semana * COST_BENEFIT_PER_RESOLUTION_BRL
roi                       = (beneficio - custo_total) / custo_total
```

Sem solicitações medidas, todos os campos derivados da amostra (`custo_api_brl`, `custo_total_brl`, `custo_por_solicitacao_brl`, `resolucoes_semana`, `custo_por_resolucao_brl`, `beneficio_brl`, `roi`) são `null`; só infra e solicitações projetadas aparecem. Com amostras, um denominador zero (sem resoluções, custo total zero) também devolve `null` em vez de um número inventado.

A chamada de consolidação da memória de longo prazo (`memoria_longo_prazo`, uma por resposta com `LONG_TERM_MEMORY_EVERY_TURNS=1`) roda em segundo plano; seu custo entra em `custo_total_brl` e nas médias de `/api/v1/costs` sem contar como solicitação.

### Como ler `GET /api/v1/costs`

Endpoint sem autenticação, como o `/metrics`. Retorna:

- `amostras`: acumulado medido por esta réplica (`solicitacoes`, `resolvidas`, `taxa_resolucao`, `custo_total_brl`, `tokens_entrada`, `tokens_saida`).
- `premissas`: `solicitacoes_por_usuario_semana`, `preco_entrada_por_1m_brl`, `preco_saida_por_1m_brl`, `beneficio_por_resolucao_brl`, `infra_semanal_100_brl`, `infra_semanal_1000_brl`.
- `cenarios`: um item para 100 e outro para 1000 usuários, com as grandezas acima (`solicitacoes_semana`, `custo_api_brl`, `custo_infra_brl`, `custo_total_brl`, `custo_por_solicitacao_brl`, `resolucoes_semana`, `custo_por_resolucao_brl`, `beneficio_brl`, `roi`).

Atenção: `amostras` fica na memória do processo, é **por réplica** e zera a cada restart. Com várias réplicas ou para histórico, use as consultas Prometheus abaixo, que somam todas as réplicas.

### Consultas PromQL (entre réplicas)

```promql
# Custo estimado de API (R$/h)
sum(increase(ecociente_chat_cost_brl_total[1h]))

# Taxa de resolução (1h)
sum(increase(ecociente_chats_total{resolved="true"}[1h])) / sum(increase(ecociente_chats_total[1h]))

# Custo por resolução (R$, 1h)
sum(increase(ecociente_chat_cost_brl_total[1h])) / sum(increase(ecociente_chats_total{resolved="true"}[1h]))
```

Os três estão no dashboard "EcoCiente IA" (seção "Custo e limites"). As taxas do dashboard contam apenas respostas do `/chat` concluídas; erros 5xx e recusas 429 não entram no denominador (o painel de índice de erros cobre esses casos). O custo por resolução do painel é só de API; a infra entra nos cenários de `/api/v1/costs`.

### Exemplo numérico (ilustrativo, não é medição)

Premissas inventadas só para mostrar a conta: `q = 2`, `COST_INFRA_WEEKLY_BRL_100 = 1.0`, `COST_BENEFIT_PER_RESOLUTION_BRL = 0.5`. Amostra: 10 solicitações medidas, cada uma custando R$ 0,01, das quais 8 resolvidas.

Cenário de 100 usuários:

```text
solicitacoes_semana   = 100 * 2             = 200
custo_api             = 200 * 0,01          = R$ 2,00
custo_total           = 2,00 + 1,00         = R$ 3,00
custo_por_solicitacao = 3,00 / 200          = R$ 0,015
resolucoes_semana     = 200 * (8 / 10)      = 160
custo_por_resolucao   = 3,00 / 160          = R$ 0,01875
beneficio             = 160 * 0,5           = R$ 80,00
roi                   = (80,00 - 3,00) / 3,00 = 25,67 (2567%)
```

Nenhum desses números é resultado de medição.

### O que ainda depende do grupo

- Preços do provedor ativo: **informar** em `LLM_PRICE_INPUT_PER_1M_BRL` e `LLM_PRICE_OUTPUT_PER_1M_BRL`.
- `q`: **medir** e informar em `COST_REQUESTS_PER_USER_WEEK`.
- Infra por cenário: **medir/cotar** e informar em `COST_INFRA_WEEKLY_BRL_100` e `COST_INFRA_WEEKLY_BRL_1000`.
- Benefício por resolução: **informar** em `COST_BENEFIT_PER_RESOLUTION_BRL`.
- Latência (média, mediana, p95, p99) e throughput: **medir** com `scripts/benchmark_api.py` (tabela da seção "Latência").

## Benchmark

```bash
APP_ENV=test ALLOW_TEST_IDENTITY_HEADERS=true LLM_PROVIDER=mock EMBEDDING_PROVIDER=mock \
STORAGE_MODE=memory ENABLE_EXTERNAL_SOURCE=false python -m uvicorn src.api.main:app
python scripts/benchmark_api.py --requests 100 --concurrency 10
```

O benchmark calcula média, mediana, p95, p99, taxa de erro e throughput.
