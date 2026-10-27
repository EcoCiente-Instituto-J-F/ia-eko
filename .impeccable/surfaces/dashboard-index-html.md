---
version: 1
slug: "dashboard-index-html"
primary_target: "dashboard/index.html"
related_targets: []
---

# Painel de controle (dashboard/)

Modo: Operate. Público: equipe do grupo, no dia a dia e em demonstrações. Tarefa: ver, num olhar, se o sistema está saudável, onde o tempo vai e quanto custa. Dados ao vivo de `/metrics`, `/health` e `/api/v1/costs`, com modo demo rotulado. Estrutura escolhida pelo usuário: mesa de operações em 3 colunas (Agora, Fluxo, Projeção). Build code-led (sem geração de imagem).

## Direction contract

THESIS: uma mesa de plantão em que o fluxo de agentes é desenhado como uma linha de metrô vertical com a latência de cada estação; recusa a grade de cartões iguais com número grande.

OWN-WORLD: identidade EcoCiente do Figma. Cabeçalho e faixa de indicadores em verde profundo #064E3B com números em creme #F8F4F0; corpo em creme com divisórias finas #A7B7AA; latência em azul-petróleo #205867, custo e saúde em verde #2E5C41, erro e alerta em #D64573/#B93C63. Títulos em Nunito ExtraBold, interface em Plus Jakarta Sans SemiBold, valores em monoespaçada com numerais tabulares. Ícones de contorno arredondado (lucide). Estado nunca só por cor: sempre rótulo ou forma.

STORY: quem abre sabe em 5 segundos se está ao vivo ou em demonstração, se há erro e quanto custou; depois lê onde o tempo se perde entre os agentes e simula 100 e 1000 usuários com premissas editáveis.

FIRST VIEWPORT: 1440x900. Barra verde de ~64px com árvore creme, nome, estado (ao vivo/demo), modelo, cotação e tempo. Logo abaixo a faixa verde de 6 indicadores (requisições, custo, resolução, erros, p95, custo por resolução). Abaixo, três colunas 28/44/28%: Agora (taxa de requisições, eventos, rotas), Fluxo (linha de metrô com 9 estações e barras de latência, ocupando a coluna toda), Projeção (100 e 1000 usuários, custo, ROI, simulador). O Fluxo é o centro e o maior elemento.

FORM: mesa de operações em 3 colunas, posição 7 da lista, seed 98c532d7. Movimento: uma única animação autoral, a linha do fluxo pulsa de estação em estação quando chega tráfego; reduced-motion desliga.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
