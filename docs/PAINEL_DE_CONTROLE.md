# Painel de controle (dashboard)

Painel web de observabilidade do ia-eko, em `dashboard/` (TypeScript + Vite, sem framework de UI). Mostra requisições, custo até agora, taxa de resolução, índice de erros, latência por etapa do fluxo de agentes, rotas do orquestrador e a projeção de custo, custo por resolução e ROI para 100 e 1000 usuários semanais.

O relatório estático em PDF (sem animações) é um entregável separado e usa os mesmos números e fórmulas descritos aqui.

## Como rodar

```bash
cd dashboard
npm install
npm run dev          # http://localhost:5173
npm run build        # gera dashboard/dist
npm test             # 17 testes (leitura do /metrics, percentis, custo)
npm run typecheck
```

A API precisa estar no ar em `http://127.0.0.1:8000` (ou defina `VITE_API_URL`). O backend não habilita CORS; por isso o Vite repassa `/metrics`, `/health` e `/api` para ele (proxy em `vite.config.ts`, também no `vite preview`). Nenhuma alteração no backend foi necessária.

## De onde vêm os dados

| Fonte | Para quê | Frequência |
|---|---|---|
| `GET /metrics` (Prometheus) | requisições, erros, latências, tokens, custo, resolução, rotas, juiz, guardrail, cota | 2 s |
| `GET /api/v1/costs` | premissas do backend (requisições por usuário, infra, benefício) e conferência dos cenários | a cada 6 s |
| `GET /health` | selo "API saudável / degradada" | a cada 6 s |
| `api.frankfurter.dev` | cotação US$ → R$ (gratuita, sem token), cache de 6 h | ao abrir |

Se a API não responde duas vezes seguidas, o painel entra sozinho em **modo demonstração** e tenta voltar ao vivo a cada 10 s. Também dá para escolher o modo no seletor do topo. Em demonstração:

- uma faixa rosa no topo e o selo "Demonstração · dados simulados" ficam sempre visíveis;
- todos os números são **simulados** por `src/data/demo.ts` (latências, taxas e tokens inventados, com semente fixa); nada ali foi medido;
- os selos das premissas mostram "demo" em vez de "medido".

### O que é medido, estimado ou premissa

Cada campo editável da projeção carrega um selo:

- **medido**: média das respostas lidas do `/metrics` (exige pelo menos 5 respostas);
- **demo**: o mesmo, mas vindo do gerador de demonstração;
- **do backend**: valor declarado em `/api/v1/costs` (`COST_*` do ambiente);
- **premissa**: valor assumido pelo painel (cache hit 50%, tráfego no pico 20,8%, 2.400 tokens de entrada e 380 de saída quando ainda não há medição, 85% de resolução);
- **editado**: alterado por você (fica salvo no navegador; "Restaurar medidos" limpa).

Nenhum valor monetário é inventado: infra e benefício por resolução vêm do backend (padrão R$ 0) e, enquanto estiverem em zero, o ROI aparece como "—".

## Telas e indicadores

**Faixa de indicadores** (acumulado desde o início do processo): requisições (e por minuto), custo até agora (e por resposta), respostas resolvidas, índice de erros (vira alerta a partir de 5%), tempo total p95 (e mediana), custo por resolução.

**Agora**: requisições por minuto (últimos 5 min, faixas de 10 s, falhas 5xx em rosa), eventos, rotas do orquestrador, e quadro de juiz/guardrail/cota/tokens. O backend não expõe lista de requisições; os **eventos são derivados da diferença entre duas leituras do `/metrics`** (ex.: "3 respostas (2 resolvidas)", "1 falha 5xx").

**Fluxo dos agentes**: tempo total do `/chat` (mediana, p95, p99, média) e uma linha de metrô vertical com uma estação por nó do grafo (`guardrail_entrada` → `memoria` → `verificar_compactacao` → `resumir_memoria` → `orquestrador` → especialista → `juiz_saida` → `correcao` → `guardrail_saida`). Cada estação mostra chamadas, p95 e barras de mediana e p95. A estação com maior soma de tempo recebe o selo "maior fatia do tempo". A janela é 1 min, 5 min ou desde o início. Losango = etapa que decide.

- **Latência interagentes**: a linha "Entre etapas" é `tempo médio total do /chat − soma dos tempos médios das etapas`, ou seja, tempo fora dos nós do grafo (as métricas atuais não o detalham).
- **Onde o tempo vai**: fatia de cada fase (guardrails, memória, orquestrador, especialistas, juiz e correção, entre etapas) e quanto do tempo das etapas é chamada ao LLM.
- Percentis vêm dos buckets do histograma Prometheus por interpolação linear (mesma ideia do `histogram_quantile`): são estimativas, limitadas pela resolução dos buckets (o último bucket finito é 10 s). Janelas de 1 e 5 min usam a diferença entre duas fotos do `/metrics`.

**Projeção semanal**: tabela com 100 usuários, 1.000 usuários e um cenário livre (controle deslizante). Linhas: solicitações, custo por solicitação (e faixa melhor a pior), custo de API, infra, custo total, resoluções, custo por resolução, benefício e ROI. Abaixo, as premissas editáveis. Em modo ao vivo há uma linha de conferência com os cenários do `/api/v1/costs`.

## Fórmulas

Mesmas de `docs/SRE_E_CUSTOS.md`, com o preço da DeepSeek separado por cache e horário (o `cost.py` do backend ainda usa um único preço de entrada):

```text
preço(campo)         = fatia_pico * pico(campo) + (1 - fatia_pico) * fora_do_pico(campo)
custo_resposta (US$) = tokens_entrada * (cache_hit * preço(entrada_hit) + (1 - cache_hit) * preço(entrada_miss))
                     + tokens_saida * preço(saída)          (tudo / 1.000.000)
custo_por_solicitação = custo_resposta * cotação_USD_BRL
solicitações_semana   = usuários * solicitações_por_usuário
custo_api             = solicitações_semana * custo_por_solicitação
custo_total           = custo_api + infra_do_cenário
resoluções_semana     = solicitações_semana * taxa_de_resolução
custo_por_resolução   = custo_total / resoluções_semana   (sem resoluções: "—")
benefício             = resoluções_semana * benefício_por_resolução
ROI                   = (benefício - custo_total) / custo_total   (custo total 0: "—")
```

Faixa por solicitação: **pior caso** = tudo no pico e 0% de cache; **melhor caso** = fora do pico e 90% de cache.

### Preços do deepseek-flash (sem thinking)

Fonte: https://api-docs.deepseek.com/quick_start/pricing/ (consultada em 26/10/2026). Modelo `deepseek-flash` (DeepSeek-V4.1-Flash-1017). US$ por 1M de tokens:

| | Pico | Fora do pico |
|---|---|---|
| Entrada, cache hit | 0,006 | 0,003 |
| Entrada, cache miss | 0,30 | 0,15 |
| Saída | 1,20 | 0,60 |

Pico: 01h–04h e 06h–10h UTC, de segunda a sexta (exceto feriados na China). A fatia de pico padrão, 20,8% (35 h de 168 h), vale se o uso for uniforme na semana; ajuste conforme o uso real. Cotação de referência em 26/10/2026: US$ 1 = R$ 5,5884 (Frankfurter). Os preços ficam em `src/data/preco.ts`; a DeepSeek pode reajustá-los.

## Estrutura do código

```text
dashboard/
  index.html, vite.config.ts, tsconfig.json
  public/brand/            logo e árvore (SVG exportados do Figma, versões creme)
  src/
    main.ts                monta a página, liga fonte de dados, abas, pausa, modo
    types.ts               Snapshot, Estado, respostas da API
    data/
      prom.ts              lê o texto do /metrics e normaliza (testado)
      quantil.ts           percentis por bucket, janelas (testado)
      simular.ts           custo, custo por resolução, ROI (testado)
      preco.ts, cotacao.ts preços DeepSeek e cotação Frankfurter
      fonte.ts             polling, histórico, queda para demo e retorno
      demo.ts              gerador de dados de demonstração
    ui/
      derivar.ts           visão do fluxo, taxa por minuto, série de requisições
      topo.ts, agora.ts, fluxo.ts, projecao.ts, util.ts
    styles/                tokens.css (paleta da marca), base.css, painel.css
```

## Identidade visual

Tokens em `src/styles/tokens.css`, vindos do Figma ("02 · Identidade Visual"): verdes `#064E3B`, `#2E5C41`, `#A7B7AA`, azuis-petróleo `#1D464D` e `#205867`, rosas `#B93C63` e `#D64573`, preto `#0F1723`, creme `#F8F4F0`. Fontes: Nunito ExtraBold (títulos), Plus Jakarta Sans (interface), JetBrains Mono (valores, com numerais tabulares), todas hospedadas localmente. Ícones de contorno arredondado (lucide, o mesmo kit do Figma). Estado nunca depende só da cor: há rótulos, selos e formas (losango, "ALTO", "maior fatia do tempo").

A única animação é o pulso que percorre a linha do fluxo quando chegam requisições; `prefers-reduced-motion` a desliga.

## Limitações conhecidas

- Os contadores vêm de uma réplica; com várias réplicas, aponte `VITE_API_URL` para um agregador ou use o Prometheus/Grafana.
- Se a API reiniciar, os contadores zeram; o painel avisa nos eventos e as janelas de 1 e 5 min se recompõem sozinhas.
- O índice de erros considera só falhas 5xx (`ecociente_http_request_errors_total`).
- `process_start_time_seconds` só existe no Linux; no Windows o cabeçalho mostra "Observando há" em vez de "API rodando há".
- Sem dados reais de latência, erro e resolução (a tabela de `SRE_E_CUSTOS.md` ainda diz "VALOR A MEDIR"): o painel não preenche esses valores por conta própria.
