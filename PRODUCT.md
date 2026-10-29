# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

TypeScript com Vite, escolhido pelo usuário. O painel é uma app front-end separada em `dashboard/`, sem framework de UI definido (decisão de implementação ainda aberta).

## Users

Equipe do grupo que desenvolve o ia-eko, acompanhando o sistema rodando no dia a dia e usando o painel para mostrar os números do trabalho. O usuário pediu que seja de uso interno, mas sem perder a identidade visual e com impacto.

## Product Purpose

Painel de controle de observabilidade/SRE do ia-eko (EcoCiente IA): mostra, quase em tempo real, requisições, custo acumulado, tempo rodando, taxa de resolução, índice de erros, latência por etapa do fluxo de agentes, rotas escolhidas pelo orquestrador e um simulador de custo/ROI para 100 e 1000 usuários semanais. Existe também um relatório estático (PDF, sem animações) para o mesmo requisito, feito depois.

## Positioning

Observabilidade feita sob medida para um sistema multiagente (LangGraph) de educação ambiental: mostra a latência entre agentes, o custo por resolução e o ROI, não só métricas HTTP genéricas.

## Operating Context

O sistema expõe `/metrics` (Prometheus) e `GET /api/v1/costs` (custo, custo por resolução e ROI para 100 e 1000 usuários). Já existe um dashboard Grafana (`observability/grafana/dashboards/ecociente.json`). Provedor de LLM principal: DeepSeek, modelo `deepseek-flash` sem thinking. Cotação USD→BRL obtida da API Frankfurter (api.frankfurter.dev), sem token. Os dados do painel vêm ao vivo, com modo demo.

## Capabilities and Constraints

- Dados ao vivo de `/metrics` e `/api/v1/costs`; quando a API estiver fora, modo demo com dados de demonstração claramente rotulados como tais.
- Nenhum valor inventado pode aparecer como medição. Sem amostras, campos derivados são `null` (convenção do backend).
- Definição de resolução: resposta final aprovada pelo juiz de saída.
- Preços DeepSeek Flash (US$ por 1M tokens): entrada cache miss 0,30 pico / 0,15 fora do pico; cache hit 0,006 / 0,003; saída 1,20 / 0,60. Pico: 01–04h e 06–10h UTC, seg–sex. Cotação de 26/10/2026: US$ 1 = R$ 5,5884.
- `cost.py` hoje tem um único preço de entrada, sem separar cache hit de miss.
- Valores monetários em R$; textos em português do Brasil.
- O backend não deve ser alterado sem aprovação do usuário. A chave da DeepSeek nunca é gravada em arquivo.

## Brand Commitments

Identidade EcoCiente definida no Figma (arquivo "Inter — Design (Copy)", página "02 · Identidade Visual"), vinculante:
- Cores: #A7B7AA, #2E5C41, #064E3B, #1D464D, #205867, #B93C63, #D64573, #0F1723; creme #F8F4F0 e branco #FFFFFF nas explorações.
- Tipografia: Nunito ExtraBold e Plus Jakarta Sans SemiBold. O usuário pediu fonte monoespaçada para os valores.
- Logo da árvore-cérebro com versões para fundo claro e escuro; ícones de contorno arredondados (kit Iconify).
- O usuário quer algo impactante, sem perder a identidade visual.

## Evidence on Hand

Métricas e fórmulas documentadas em `docs/SRE_E_CUSTOS.md`. Nenhum número real de latência, erro ou resolução foi medido ainda: toda a tabela de latência está como "VALOR A MEDIR". Os assets do logo ainda não foram exportados do Figma.

## Product Principles

- Honestidade dos números: medido, estimado e demonstração nunca se confundem.
- Latência entre agentes é o diferencial e precisa ser visível por etapa do fluxo.
- Custo sempre acompanhado de resolução: custo por resolução e ROI são os números finais.
- Tudo documentado, incluindo fórmulas e premissas.

## Accessibility & Inclusion

Nenhum requisito específico estabelecido. O estado não pode depender só da cor (importante com verdes e rosas da paleta).
