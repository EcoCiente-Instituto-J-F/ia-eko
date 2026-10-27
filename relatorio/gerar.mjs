// Gera relatorio.html a partir das medições em relatorio/dados/ e do mesmo código de cálculo do painel.
// Uso (a partir da raiz do repositório):  node relatorio/gerar.mjs
// Depende de dashboard/node_modules (npm install em dashboard/).
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";
import { arte, PALETA_ROSA, PALETA_VERDE } from "./arte.mjs";

const aqui = dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));
const raiz = join(aqui, "..");
const dash = join(raiz, "dashboard");
const dados = join(aqui, "dados");

const require = createRequire(join(dash, "package.json"));
const { createServer } = await import(pathToFileURL(require.resolve("vite")).href);
const vite = await createServer({ root: dash, server: { middlewareMode: true }, appType: "custom", logLevel: "error" });
const { normalizar } = await vite.ssrLoadModule("/src/data/prom.ts");
const { quantil, media } = await vite.ssrLoadModule("/src/data/quantil.ts");
const { simular, faixaPorSolicitacaoBrl } = await vite.ssrLoadModule("/src/data/simular.ts");
const { USD_POR_1M, FATIA_PICO_UNIFORME } = await vite.ssrLoadModule("/src/data/preco.ts");

// ───────── medições ─────────
function bench(arquivo) {
  const t = readFileSync(join(dados, arquivo), "utf8");
  const g = (k) => Number(new RegExp(`${k}=([0-9.]+)`).exec(t)[1]);
  return {
    requisicoes: g("requests"),
    concorrencia: g("concurrency"),
    media: g("mean_ms"),
    mediana: g("median_ms"),
    p95: g("p95_ms"),
    p99: g("p99_ms"),
    erro: g("error_rate"),
    rps: g("throughput_rps"),
  };
}
const b1 = bench("bench-c1.txt");
const b10 = bench("bench-c10.txt");
const snap = normalizar(readFileSync(join(dados, "metrics.txt"), "utf8"), Date.now());
const custosBackend = JSON.parse(readFileSync(join(dados, "costs.json"), "utf8"));

const ORDEM = ["guardrail_entrada", "memoria", "verificar_compactacao", "resumir_memoria", "orquestrador", "faq", "analytics", "educacional", "coletas", "grafo", "juiz_saida", "correcao", "guardrail_saida"];
const ROTULO = {
  guardrail_entrada: "Guardrail de entrada",
  memoria: "Memória",
  verificar_compactacao: "Verificar compactação",
  resumir_memoria: "Resumir memória",
  orquestrador: "Orquestrador",
  faq: "FAQ",
  analytics: "Analytics",
  educacional: "Educacional",
  coletas: "Coletas",
  grafo: "Grafo",
  juiz_saida: "Juiz de saída",
  correcao: "Correção",
  guardrail_saida: "Guardrail de saída",
};
const nomes = [...ORDEM.filter((n) => snap.agentes[n]), ...Object.keys(snap.agentes).filter((n) => !ORDEM.includes(n))];
const somaNos = nomes.reduce((a, n) => a + snap.agentes[n].sum, 0);
const nos = nomes.map((n) => {
  const h = snap.agentes[n];
  return { id: n, rotulo: ROTULO[n] ?? n, chamadas: h.count, media: media(h), p50: quantil(h, 0.5), p95: quantil(h, 0.95), fatia: h.sum / somaNos };
});
const chat = { n: snap.chat.count, media: media(snap.chat), p50: quantil(snap.chat, 0.5), p95: quantil(snap.chat, 0.95), p99: quantil(snap.chat, 0.99) };
const respostas = snap.chats.resolvidas + snap.chats.naoResolvidas;
const entreEtapas = Math.max(0, chat.media - somaNos / chat.n);
const maxP95 = Math.max(...nos.map((n) => n.p95 ?? 0));

// ───────── custo (mesmas funções do painel) ─────────
const USD_BRL = 5.5884;
const DATA_COTACAO = "26/10/2026";
const PREM = { q: 5, tokIn: 2400, tokOut: 380, resolucao: 0.85, cacheEsperado: 0.5 };
const casos = [
  { id: "melhor", rotulo: "Melhor caso", desc: "fora do pico, 90% de cache", cache: 0.9, pico: 0 },
  { id: "esperado", rotulo: "Caso esperado", desc: `${fmt(FATIA_PICO_UNIFORME * 100, 1)}% no pico, 50% de cache`, cache: 0.5, pico: FATIA_PICO_UNIFORME },
  { id: "pior", rotulo: "Pior caso", desc: "tudo no pico, sem cache", cache: 0, pico: 1 },
];
const entrada = (usuarios, c) => ({
  usuarios,
  solicitacoesPorUsuarioSemana: PREM.q,
  tokensEntradaPorResposta: PREM.tokIn,
  tokensSaidaPorResposta: PREM.tokOut,
  cacheHit: c.cache,
  fatiaPico: c.pico,
  resolucao: PREM.resolucao,
  usdBrl: USD_BRL,
  infraSemanalBrl: 0,
  beneficioPorResolucaoBrl: 0,
});
const cenarios = [100, 1000].map((u) => ({ usuarios: u, casos: casos.map((c) => ({ ...c, r: simular(entrada(u, c)) })) }));
const faixa = faixaPorSolicitacaoBrl(PREM.tokIn, PREM.tokOut, USD_BRL);
const esperado = (u) => cenarios.find((c) => c.usuarios === u).casos.find((c) => c.id === "esperado").r;
const hipoteses = [0.05, 0.1, 0.25, 0.5, 1];
const roi = (u, h) => {
  const r = esperado(u);
  return (r.resolucoesSemana * h - r.custoTotalSemanaBrl) / r.custoTotalSemanaBrl;
};

// ───────── formatação ─────────
function fmt(n, d = 2) {
  return new Intl.NumberFormat("pt-BR", { minimumFractionDigits: d, maximumFractionDigits: d }).format(n);
}
const NBSP = " ";
const brl = (n, d) => `R$${NBSP}${fmt(n, d ?? (n >= 1 ? 2 : n >= 0.01 ? 3 : 4))}`;
const bigBrl = (s) => s.replace(`R$${NBSP}`, '<span class="p">R$</span>');
const ms = (s) => (s === null ? "—" : s < 0.0001 ? `< 0,1${NBSP}ms` : s < 1 ? `${fmt(s * 1000, s * 1000 < 10 ? 1 : 0)}${NBSP}ms` : `${fmt(s, 2)}${NBSP}s`);
// o primeiro bucket do histograma vai até 5 ms: abaixo disso o percentil é só um limite superior
const msq = (s) => (s === null ? "—" : s <= 0.005 + 1e-9 ? "< 5 ms" : ms(s));
const pct = (r, d = 1) => `${fmt(r * 100, d)}%`;
const inteiro = (n) => new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 }).format(n);
const tag = (t) => {
  const mapa = { medido: "MEDIDO", estimado: "ESTIMADO", premissa: "PREMISSA", amedir: "A MEDIR" };
  return `<span class="tag tag--${t}">${mapa[t]}</span>`;
};

// ───────── conteúdo ─────────
const exp100 = esperado(100);
const exp1000 = esperado(1000);
const pior100 = cenarios[0].casos[2].r;
const melhor100 = cenarios[0].casos[0].r;
const pior1000 = cenarios[1].casos[2].r;
const melhor1000 = cenarios[1].casos[0].r;

const linhaCusto = (rot, f) => `<tr><th scope="row">${rot}</th>${cenarios.map((c) => c.casos.map((k) => `<td class="v">${f(k.r)}</td>`).join("")).join("")}</tr>`;


// ───────── ilustrações e estrutura das páginas ─────────
const TAB = [[0, "#a9dcd6"], [1, "#4a9fb5"]];
const CEU_CAPA = [[0, "#0f3a47"], [0.3, "#1d5e70"], [0.5, "#3f94a0"], [0.62, "#8fcfc8"], [0.74, "#2e5c41"], [1, "#064e3b"]];
const CEU_LADO = [[0, "#205867"], [0.35, "#4fa3b0"], [0.65, "#cde6de"], [0.82, "#a7b7aa"], [1, "#2e5c41"]];
const pos = (x, y, svg) => `<div class="a" style="left:${x}px;top:${y}px">${svg}</div>`;
const abre = (seed, cheio = false, altura) =>
  `<div class="topo${cheio ? " cheio" : ""}"${altura ? ` style="height:${altura}px"` : ""}></div>${pos(395, 0, arte({ w: 705, h: 60, seed, cols: 18, rows: 2, stops: TAB }))}`;

let n = 1; // a capa é a página 1
const CAB = "Relatório de observabilidade, custo e ROI · EcoCiente IA";
const pg = (cls, corpo) => {
  n += 1;
  return `<section class="pg ${cls}">\n${corpo}\n<div class="run">${CAB}</div><div class="num">${n}</div>\n</section>`;
};

const cabecaCasos = `<thead><tr><th rowspan="2" style="width:160px"></th><th colspan="3">100 usuários</th><th colspan="3">1.000 usuários</th></tr><tr>${[0, 1]
  .map(() => casos.map((c) => `<th class="v sub2">${c.rotulo.replace(" caso", "")}</th>`).join(""))
  .join("")}</tr></thead>`;
const ULTIMA = "26/10/2026";
const edu = nos.find((x) => x.id === "educacional");
const somaMedNos = somaNos / chat.n;
const totalRotas = Object.values(snap.rotas).reduce((a, b) => a + b, 0);

// ═════════════ 1 · CAPA ═════════════
const capa = `<section class="pg">
<div class="capa-topo"></div>
<div class="capa-chip">Relatório de SRE · 2026</div>
<img class="capa-logo" src="fontes/logo-verde.svg" alt="EcoCiente">
<div class="capa-titulo">Observabilidade,<br>custo e ROI</div>
<div class="capa-meta"><span><b>EcoCiente IA</b> (ia-eko)</span><span>${ULTIMA}</span><span>deepseek-flash · sem thinking</span><span>US$ 1 = R$ ${fmt(USD_BRL, 4)}</span><span>código 583a8c9</span></div>
${pos(0, 330, arte({ w: 1100, h: 520, seed: 7, cols: 26, rows: 13, stops: CEU_CAPA, tiras: [[160, 0, 545, 56], [412, 140, 688, 72], [0, 290, 660, 70]], arvores: [{ x: 95, y: 470, s: 120, paleta: PALETA_ROSA }, { x: 850, y: 500, s: 70, paleta: PALETA_VERDE }, { x: 955, y: 505, s: 80, paleta: PALETA_VERDE }, { x: 1040, y: 500, s: 52, paleta: PALETA_ROSA }] }))}
</section>`;

// ═════════════ 2 · COMO LER ═════════════
const intro = pg(
  "escura",
  `<div class="metade-esc"></div>
${pos(550, 0, arte({ w: 550, h: 850, seed: 11, cols: 12, rows: 18, stops: CEU_LADO, arvores: [{ x: 130, y: 800, s: 85, paleta: PALETA_VERDE }, { x: 430, y: 815, s: 70, paleta: PALETA_ROSA }] }))}
<div class="a" style="left:60px;top:118px;width:440px">
  <h2 class="grande" style="font-size:80px;line-height:74px">Cinco perguntas de SRE</h2>
  <p class="lead" style="margin-top:24px">Quanto custa, quão rápido responde, quanto erra, quanto retorna e quanto custa cada problema resolvido.</p>
</div>
<div class="vidro" style="left:600px;top:236px;width:440px">
  <h3>Como ler este relatório</h3>
  <p style="margin-bottom:16px">Cada número traz uma etiqueta de origem. Nenhum valor foi inventado: o que depende de tráfego real com a DeepSeek, de infraestrutura ou de benefício ainda não foi medido e aparece como tal.</p>
  <ul class="legenda">
    <li>${tag("medido")}<span>saiu de uma execução do sistema, com o método descrito na página ${11}.</span></li>
    <li>${tag("estimado")}<span>calculado por fórmula a partir de premissas declaradas.</span></li>
    <li>${tag("premissa")}<span>valor assumido, sem medição por trás.</span></li>
    <li>${tag("amedir")}<span>ainda não existe número; o relatório indica como obtê-lo.</span></li>
  </ul>
</div>`,
);

// ═════════════ 3 · RESUMO ═════════════
const cartao = (cls, rot, big, small, texto, tags) =>
  `<div class="card ${cls}"><div class="rot">${rot}</div><div class="big">${big}<small>${small}</small></div><p>${texto}</p><div class="ori">${tags.map(tag).join(" ")}</div></div>`;
const resumo = pg(
  "",
  `${abre(3, true)}
<div class="a" style="left:60px;top:104px;width:980px"><div class="grande">O resumo em cinco números</div><p class="sub" style="margin-top:14px">Cada cartão responde a um requisito do trabalho. As páginas seguintes mostram como chegamos a cada um.</p></div>
<div class="cinco">
  ${cartao("g-rosa", "Custo estimado", bigBrl(brl(exp100.custoApiSemanaBrl)), "por semana · 100 usuários", `Com 1.000 usuários: <b>${brl(exp1000.custoApiSemanaBrl)}</b>. Caso esperado, só API; infraestrutura não informada.`, ["estimado"])}
  ${cartao("g-verde", "Tempo de resposta", `${fmt(b10.p95, 0)}<span class="u">ms</span>`, "p95 · 10 simultâneas", `Com o LLM simulado: é o piso do sistema. Uma por vez: ${fmt(b1.p95, 0)} ms. Com a DeepSeek: a medir.`, ["medido", "amedir"])}
  ${cartao("g-azul", "Índice de erros", `${snap.http.erros5xx}<span class="u">de</span>${inteiro(chat.n)}`, "falhas 5xx no teste", `Com o LLM simulado, que nunca falha. A taxa em produção depende da DeepSeek: a medir.`, ["medido", "amedir"])}
  ${cartao("g-rosa", "Custo por resolução", bigBrl(brl(exp100.custoPorResolucaoBrl)), "por resolução · caso esperado", `Só API, com ${pct(PREM.resolucao, 0)} de resolução assumida. Infraestrutura fora da conta.`, ["estimado"])}
  ${cartao("g-verde", "Custo/retorno (ROI)", `<span class="menor">a definir</span>`, "benefício por resolução", `Fica positivo se cada resolução valer mais que <b>${brl(exp100.custoPorResolucaoBrl)}</b> (só API).`, ["estimado", "premissa"])}
</div>
<div class="nao">
  <div class="sig"><div class="rot">O que este relatório não afirma</div><p>Três limites que valem para todos os números.</p></div>
  <ul class="lista">
    <li>Que a resposta com a DeepSeek terá a latência medida aqui: o LLM foi simulado, então os tempos são do sistema sem o modelo.</li>
    <li>Que a taxa de resolução é de ${pct(snap.chats.resolvidas / respostas, 0)}: o juiz simulado aprova tudo. A taxa das contas, ${pct(PREM.resolucao, 0)}, é uma premissa.</li>
    <li>Nada sobre infraestrutura nem benefício por resolução: ambos estão em zero no sistema, por não terem sido informados.</li>
  </ul>
</div>`,
);

// ═════════════ 4 · CUSTO ═════════════
const custo1 = pg(
  "",
  `${abre(4)}
<div class="a c1 l1">
  <h2>Custo de API por semana ${tag("estimado")}</h2>
  <p class="sub"><span class="m">100 usuários custam ${brl(exp100.custoApiSemanaBrl)} por semana  e 1.000 usuários, ${brl(exp1000.custoApiSemanaBrl)},</span> no caso esperado.</p>
</div>
<div class="stat dupla g-rosa">
  <div class="rot">Caso esperado · só custo de API</div>
  <div class="par"><div class="big">${bigBrl(brl(exp100.custoApiSemanaBrl))}<small>/semana</small></div><div class="cap">100 usuários · ${inteiro(exp100.solicitacoesSemana)} solicitações</div></div>
  <div class="par"><div class="big">${bigBrl(brl(exp1000.custoApiSemanaBrl))}<small>/semana</small></div><div class="cap">1.000 usuários · ${inteiro(exp1000.solicitacoesSemana)} solicitações</div></div>
</div>
<div class="a c3 l1 corpo">
  <p>O custo de uma resposta vem dos tokens que ela consome e dos preços do <strong>deepseek-flash sem thinking</strong>, que mudam com o horário (pico ou fora do pico) e com o uso de cache.</p>
  <p>Como ainda não há tráfego real com a DeepSeek, o tráfego no pico e o cache foram tratados como faixa, do melhor caso ao pior. Mil usuários custam exatamente 10 vezes mais em API.</p>
</div>
<div class="a c1 l2 sig"><div class="rot">O que isso significa</div><p>Nesta escala a API custa centavos por semana. O que vai pesar de verdade é a infraestrutura, que ainda não foi informada.</p></div>
<div class="a c23 l2">
  <table>${cabecaCasos}<tbody>
    ${linhaCusto("Solicitações", (r) => inteiro(r.solicitacoesSemana))}
    ${linhaCusto("Custo por solicitação", (r) => brl(r.custoPorSolicitacaoBrl))}
    ${linhaCusto("Custo de API", (r) => brl(r.custoApiSemanaBrl))}
    ${linhaCusto("Resoluções", (r) => inteiro(r.resolucoesSemana))}
  </tbody></table>
  <p class="nota">Melhor caso: ${casos[0].desc}. Caso esperado: ${casos[1].desc} (tráfego uniforme na semana: 35 h de 168 h no pico). Pior caso: ${casos[2].desc}.</p>
</div>`,
);

// ═════════════ 5 · CUSTO: PREÇOS E FÓRMULAS ═════════════
const custo2 = pg(
  "",
  `${abre(5, true)}
<div class="a c1 l1">
  <h2>Como o custo é calculado</h2>
  <p class="sub"><span class="m">Preço por token da DeepSeek,  que muda com o horário e com o cache.</span></p>
</div>
<div class="cartao" style="left:397px;top:147px;width:306px">
  <h3>Preços usados (US$ por 1M de tokens)</h3>
  <table>
    <thead><tr><th></th><th class="v">Pico</th><th class="v">Fora do pico</th></tr></thead>
    <tbody>
      <tr><th scope="row">Entrada, cache hit</th><td class="v">${fmt(USD_POR_1M.pico.entradaCacheHit, 3)}</td><td class="v">${fmt(USD_POR_1M.foraDoPico.entradaCacheHit, 3)}</td></tr>
      <tr><th scope="row">Entrada, cache miss</th><td class="v">${fmt(USD_POR_1M.pico.entradaCacheMiss, 2)}</td><td class="v">${fmt(USD_POR_1M.foraDoPico.entradaCacheMiss, 2)}</td></tr>
      <tr><th scope="row">Saída</th><td class="v">${fmt(USD_POR_1M.pico.saida, 2)}</td><td class="v">${fmt(USD_POR_1M.foraDoPico.saida, 2)}</td></tr>
    </tbody>
  </table>
  <p class="nota">Pico: 01h–04h e 06h–10h UTC (22h–01h e 03h–07h em Brasília), de segunda a sexta. A DeepSeek pode reajustar os preços.</p>
</div>
<div class="cartao" style="left:733px;top:147px;width:307px">
  <h3>Premissas ${tag("premissa")}</h3>
  <table>
    <tbody>
      <tr><th scope="row">Solicitações por usuário por semana</th><td class="v">${PREM.q}</td></tr>
      <tr><th scope="row">Tokens de entrada por resposta</th><td class="v">${inteiro(PREM.tokIn)}</td></tr>
      <tr><th scope="row">Tokens de saída por resposta</th><td class="v">${inteiro(PREM.tokOut)}</td></tr>
      <tr><th scope="row">Respostas resolvidas</th><td class="v">${pct(PREM.resolucao, 0)}</td></tr>
      <tr><th scope="row">Infraestrutura por semana</th><td class="v">não informada</td></tr>
    </tbody>
  </table>
</div>
<div class="a c1 l2 sig"><div class="rot">O que isso significa</div><p>O preço é previsível: depende dos tokens e de quanto do prompt cai no cache. O que ainda pode mexer no custo é o tamanho real das respostas, que será medido com a DeepSeek.</p></div>
<div class="a c23 l2">
  <div class="rot" style="font:800 11px/14px 'Plus Jakarta Sans',sans-serif;letter-spacing:.07em;text-transform:uppercase;margin-bottom:10px">Fórmulas</div>
  <pre class="formula">preço(campo)          = fatia_pico × pico(campo) + (1 − fatia_pico) × fora_do_pico(campo)
custo da resposta US$ = [entrada × (cache × preço(hit) + (1 − cache) × preço(miss))
                         + saída × preço(saída)] ÷ 1.000.000
custo por solicitação = custo da resposta × cotação
custo de API semanal  = usuários × solicitações por usuário × custo por solicitação</pre>
  <p class="nota">Fonte dos preços: api-docs.deepseek.com/quick_start/pricing, consultada em ${ULTIMA}. Cotação: Frankfurter (api.frankfurter.dev), US$ 1 = R$ ${fmt(USD_BRL, 4)} em ${DATA_COTACAO}. As premissas são as mesmas do painel de controle e podem ser editadas lá.</p>
</div>`,
);

// ═════════════ 6 · LATÊNCIA: TEMPO TOTAL ═════════════
const lat1 = pg(
  "",
  `${abre(6)}
<div class="a c1 l1">
  <h2>Tempo total de resposta ${tag("medido")}</h2>
  <p class="sub"><span class="m">Sem o modelo, o sistema responde em ${fmt(b1.mediana, 0)} ms  (mediana, uma requisição por vez).</span></p>
</div>
<div class="stat dupla g-verde">
  <div class="rot">p95 do tempo total · LLM simulado</div>
  <div class="par"><div class="big">${fmt(b1.p95, 0)}<span class="u">ms</span></div><div class="cap">uma requisição por vez · ${b1.requisicoes} requisições</div></div>
  <div class="par"><div class="big">${fmt(b10.p95, 0)}<span class="u">ms</span></div><div class="cap">10 simultâneas · ${b10.requisicoes} requisições</div></div>
</div>
<div class="a c3 l1 corpo">
  <p>As medições vieram do sistema real rodando com o <strong>LLM simulado</strong> (<span class="v">LLM_PROVIDER=mock</span>): o modelo responde na hora.</p>
  <p>Elas mostram o custo de tempo do próprio sistema: guardrails, memória, roteamento, juiz e serialização. O tempo esperando a DeepSeek <strong>não</strong> está incluído ${tag("amedir")}.</p>
</div>
<div class="a c1 l2 sig"><div class="rot">O que isso significa</div><p>Estes tempos são um piso. O usuário só vai sentir a latência real quando o modelo da DeepSeek entrar na conta.</p></div>
<div class="a c23 l2">
  <table>
    <thead><tr><th></th><th class="v">Média</th><th class="v">Mediana</th><th class="v">p95</th><th class="v">p99</th><th class="v">Erros</th><th class="v">Vazão</th></tr></thead>
    <tbody>
      ${[b1, b10]
        .map(
          (b) =>
            `<tr><th scope="row">${b.concorrencia === 1 ? "Uma por vez" : `${b.concorrencia} simultâneas`}</th><td class="v">${fmt(b.media, 1)} ms</td><td class="v">${fmt(b.mediana, 1)} ms</td><td class="v">${fmt(b.p95, 1)} ms</td><td class="v">${fmt(b.p99, 1)} ms</td><td class="v">${fmt(b.erro, 2)}%</td><td class="v">${fmt(b.rps, 1)} req/s</td></tr>`,
        )
        .join("")}
    </tbody>
  </table>
  <p class="nota">Medido pelo cliente, com <span class="v">scripts/benchmark_api.py</span>. Máquina de desenvolvimento (Windows, Python 3.14), sem rede externa, sem bancos, API e cliente no mesmo computador. Resultado de uma execução; não é capacidade de produção.</p>
</div>`,
);

// ═════════════ 7 · LATÊNCIA: POR ETAPA ═════════════
const lat2 = pg(
  "",
  `${abre(7, true)}
<div class="a c1 l1">
  <h2>Latência entre agentes ${tag("medido")}</h2>
  <p class="sub"><span class="m">O especialista educacional concentra ${pct(edu ? edu.fatia : 0, 0)}  do tempo das etapas do grafo.</span></p>
  <p class="corpo" style="margin-top:14px">O roteamento caiu sempre nele (${inteiro(snap.rotas.educacional ?? 0)} de ${inteiro(totalRotas)} decisões); as outras rotas não foram exercitadas.</p>
</div>
<div class="a c23" style="top:130px">
  <table>
    <thead><tr><th>Etapa</th><th class="v">Chamadas</th><th class="v">Média</th><th class="v">p50</th><th class="v">p95</th><th>Escala do p95 (0 a ${ms(maxP95)})</th></tr></thead>
    <tbody>
      ${nos
        .map(
          (x) =>
            `<tr><th scope="row">${x.rotulo}</th><td class="v">${inteiro(x.chamadas)}</td><td class="v">${ms(x.media)}</td><td class="v">${msq(x.p50)}</td><td class="v">${msq(x.p95)}</td><td class="bar"><span class="barra"><span style="width:${Math.max(1.5, ((x.p95 ?? 0) / maxP95) * 100).toFixed(1)}%"></span></span></td></tr>`,
        )
        .join("")}
      <tr class="soma"><th scope="row">Entre as etapas</th><td class="v">—</td><td class="v">${ms(entreEtapas)}</td><td class="v">—</td><td class="v">—</td><td>fora das etapas do grafo</td></tr>
      <tr class="soma"><th scope="row">Total (servidor)</th><td class="v">${inteiro(chat.n)}</td><td class="v">${ms(chat.media)}</td><td class="v">${msq(chat.p50)}</td><td class="v">${msq(chat.p95)}</td><td></td></tr>
    </tbody>
  </table>
</div>
<div class="a c1 l2 sig"><div class="rot">O que isso significa</div><p>Das ${ms(chat.media)} de média no servidor, só ${ms(somaMedNos)} estão nas etapas; o resto fica fora delas e as métricas atuais não o detalham.</p></div>
<div class="a c2 l2 corpo">
  <div class="rot" style="font:800 11px/14px 'Plus Jakarta Sans',sans-serif;letter-spacing:.07em;text-transform:uppercase;margin-bottom:9px">O que muda com a DeepSeek ${tag("amedir")}</div>
  <p>No caminho padrão o grafo encadeia o orquestrador, o especialista e o juiz de saída, e cada um faz ao menos uma chamada ao LLM; se o juiz reprova, entram a correção e um novo juízo. FAQ via Qdrant e coletas não passam por juiz LLM.</p>
  <p>O tempo total com a DeepSeek será o do sistema somado às chamadas ao modelo, o gargalo esperado.</p>
</div>
<div class="a c3 l2 corpo">
  <div class="rot" style="font:800 11px/14px 'Plus Jakarta Sans',sans-serif;letter-spacing:.07em;text-transform:uppercase;margin-bottom:9px">Como medir</div>
  <p>Rodar o benchmark com <span class="v">LLM_PROVIDER=deepseek</span> e a chave no ambiente, e ler a latência por etapa e por chamada em <span class="v">ecociente_agent_duration_seconds</span> e <span class="v">ecociente_llm_duration_seconds</span>.</p>
  <p>O projeto já alerta se o p95 do <span class="v">/chat</span> passar de 15 s por 10 minutos (<span class="v">alerts.yml</span>).</p>
  <p class="nota">p50 e p95 são interpolações dos buckets do histograma (o menor vai até 5 ms): valem como ordem de grandeza. A média é exata.</p>
</div>`,
);

// ═════════════ 8 · ERROS ═════════════
const erros = pg(
  "",
  `${abre(8)}
<div class="a c1 l1">
  <h2>Índice de erros ${tag("medido")}</h2>
  <p class="sub"><span class="m">${snap.http.erros5xx} falhas em ${inteiro(chat.n)} requisições  no teste com o LLM simulado.</span></p>
</div>
<div class="stat g-azul">
  <div class="rot">Falhas 5xx · LLM simulado</div>
  <div class="big">${snap.http.erros5xx}<span class="u">de</span>${inteiro(chat.n)}</div>
  <div class="cap">${pct(snap.http.erros5xx / chat.n, 2)} das requisições</div>
  <div class="ori">${tag("medido")} ${tag("amedir")}</div>
</div>
<div class="a c3 l1 corpo">
  <p><strong>Definição:</strong> índice de erros = requisições com falha de servidor (HTTP 5xx ou exceção) ÷ requisições totais.</p>
  <p>Respostas 4xx (cliente), bloqueios do guardrail e recusas por limite de uso não entram: aparecem em contadores próprios.</p>
</div>
<div class="a c1 l2 sig"><div class="rot">O que isso significa</div><p>Zero erros prova pouco: o modelo simulado nunca falha, atrasa ou devolve saída inválida. A taxa real depende da DeepSeek.</p></div>
<div class="a c23 l2">
  <table>
    <thead><tr><th>Sinal</th><th class="v">Resultado do teste</th><th class="v">Métrica</th></tr></thead>
    <tbody>
      <tr><th scope="row">Falhas 5xx</th><td class="v">${snap.http.erros5xx} de ${inteiro(chat.n)} (${pct(snap.http.erros5xx / chat.n, 2)})</td><td class="v" style="font-size:10px">ecociente_http_request_errors_total</td></tr>
      <tr><th scope="row">Respostas reprovadas pelo juiz</th><td class="v">${inteiro(snap.juiz.reprovadas)} de ${inteiro(snap.juiz.aprovadas + snap.juiz.reprovadas)}</td><td class="v" style="font-size:10px">ecociente_judge_decisions_total</td></tr>
      <tr><th scope="row">Entradas barradas pelo guardrail</th><td class="v">${inteiro(Object.values(snap.guardrail).reduce((a, b) => a + b, 0))}</td><td class="v" style="font-size:10px">ecociente_guardrail_blocked_total</td></tr>
      <tr><th scope="row">Recusadas por limite de uso</th><td class="v">${inteiro(snap.cota)}</td><td class="v" style="font-size:10px">ecociente_quota_rejected_total</td></tr>
    </tbody>
  </table>
  <p class="nota">A carga foi pequena (${inteiro(chat.n)} requisições) e sem timeouts nem limites de uso da DeepSeek. Para acompanhar em produção: <span class="v">sum(rate(ecociente_http_request_errors_total[5m])) / sum(rate(ecociente_http_requests_total[5m]))</span>.</p>
</div>`,
);

// ═════════════ 9 · CUSTO POR RESOLUÇÃO ═════════════
const resol = pg(
  "",
  `${abre(9)}
<div class="a c1 l1">
  <h2>Custo por resolução ${tag("estimado")}</h2>
  <p class="sub"><span class="m">Cada resolução custa ${brl(exp100.custoPorResolucaoBrl)} de API  no caso esperado.</span></p>
</div>
<div class="stat g-rosa">
  <div class="rot">Por resolução · caso esperado · só API</div>
  <div class="big">${bigBrl(brl(exp100.custoPorResolucaoBrl))}</div>
  <div class="cap">Faixa: ${brl(melhor100.custoPorResolucaoBrl)} (melhor caso) a ${brl(pior100.custoPorResolucaoBrl)} (pior caso)</div>
  <div class="ori">${tag("estimado")} ${tag("premissa")}</div>
</div>
<div class="a c3 l1 corpo">
  <p><strong>Definição:</strong> uma solicitação é <em>resolvida</em> quando a resposta final é aprovada pelo juiz de saída. Bloqueios do guardrail e respostas reprovadas após as correções não contam.</p>
  <p><span class="v">custo por resolução = custo total ÷ resoluções</span>. As bloqueadas e reprovadas entram no custo, então quanto menos resolve, mais cada resolução custa.</p>
</div>
<div class="a c1 l2 sig"><div class="rot">O que isso significa</div><p>O custo por resolução é a métrica que une custo e qualidade: melhorar a taxa de aprovação do juiz reduz o custo de cada problema resolvido.</p></div>
<div class="a c23 l2">
  <table>${cabecaCasos}<tbody>
    ${linhaCusto("Custo de API por resolução", (r) => brl(r.custoPorResolucaoBrl))}
  </tbody></table>
  <p class="nota">Com ${pct(PREM.resolucao, 0)} de resolução assumida. Não inclui infraestrutura, que ainda não foi informada: ao informá-la, soma-se ao custo total antes de dividir.</p>
</div>`,
);

// ═════════════ 10 · ROI ═════════════
const roiPg = pg(
  "",
  `${abre(10)}
<div class="a c1 l1">
  <h2>Custo/retorno (ROI) ${tag("estimado")}</h2>
  <p class="sub"><span class="m">Sem o benefício por resolução definido,  não existe um ROI único.</span></p>
</div>
<div class="stat g-verde">
  <div class="rot">Ponto de equilíbrio · caso esperado · só API</div>
  <div class="big">${bigBrl(brl(exp100.custoPorResolucaoBrl))}</div>
  <div class="cap">benefício mínimo por resolução para o ROI deixar de ser negativo</div>
  <div class="ori">${tag("estimado")} ${tag("premissa")}</div>
</div>
<div class="a c3 l1 corpo">
  <p><strong>Fórmula:</strong></p>
  <pre class="formula" style="margin:6px 0 12px;font-size:10.5px">ROI = (benefício − custo total)
      ÷ custo total
benefício = resoluções
          × benefício por resolução</pre>
  <p>O benefício por resolução (por exemplo, um atendimento humano evitado) é uma decisão do grupo e ainda não foi informado.</p>
</div>
<div class="a c1 l2 sig"><div class="rot">O que isso significa</div><p>O grupo precisa escolher quanto vale uma resolução. A tabela mostra o que cada escolha resultaria.</p></div>
<div class="a c23 l2">
  <table>
    <thead><tr><th>Benefício por resolução (hipótese)</th>${hipoteses.map((h) => `<th class="v">${brl(h, 2)}</th>`).join("")}</tr></thead>
    <tbody>
      <tr><th scope="row">ROI com 100 usuários</th>${hipoteses.map((h) => `<td class="v">${roi(100, h) >= 0 ? "+" : ""}${inteiro(roi(100, h) * 100)}%</td>`).join("")}</tr>
      <tr><th scope="row">ROI com 1.000 usuários</th>${hipoteses.map((h) => `<td class="v">${roi(1000, h) >= 0 ? "+" : ""}${inteiro(roi(1000, h) * 100)}%</td>`).join("")}</tr>
    </tbody>
  </table>
  <p class="nota">Os valores do cabeçalho são hipóteses de leitura, não estimativas do grupo. Caso esperado, só custo de API (infraestrutura em zero). Os percentuais são altos porque o custo de API por resolução é de frações de centavo; o ROI real cairá quando a infraestrutura for informada. É igual nos dois cenários porque a API escala linearmente; só diverge quando a infraestrutura de cada cenário for informada.</p>
</div>`,
);

// ═════════════ 11 · MÉTODO E LIMITAÇÕES ═════════════
const metodo = pg(
  "",
  `${abre(11, true, 850)}
<div class="a c1 l1"><div class="grande" style="font-size:72px;line-height:66px">Método e limitações</div><p class="sub" style="margin-top:18px"><span class="m">Tudo o que foi medido veio de uma máquina e de uma execução.</span></p></div>
<div class="a c2 l1 corpo">
  <div class="rot" style="font:800 11px/14px 'Plus Jakarta Sans',sans-serif;letter-spacing:.07em;text-transform:uppercase;margin-bottom:12px">Como as medições foram feitas</div>
  <ol class="passos">
    <li>API do ia-eko iniciada localmente com <span class="v">LLM_PROVIDER=mock</span>, <span class="v">EMBEDDING_PROVIDER=mock</span> e <span class="v">STORAGE_MODE=memory</span> (sem bancos, sem rede externa).</li>
    <li>Duas execuções de <span class="v">scripts/benchmark_api.py</span>, de 200 requisições cada, com concorrência 1 e 10; saídas em <span class="v">relatorio/dados/</span>.</li>
    <li>Leitura do <span class="v">/metrics</span> e do <span class="v">/api/v1/costs</span> ao final, com os mesmos histogramas que o painel usa. Percentis por interpolação linear nos buckets.</li>
    <li>Custos pelas funções <span class="v">simular</span> e <span class="v">faixaPorSolicitacaoBrl</span> do painel (<span class="v">dashboard/src/data/simular.ts</span>), testadas: relatório e painel mostram os mesmos números.</li>
  </ol>
  <p class="nota" style="margin-top:14px">Para regerar: <span class="v">node relatorio/gerar.mjs</span> a partir da raiz, com <span class="v">npm install</span> feito em <span class="v">dashboard/</span>.</p>
</div>
<div class="a c3 l1 corpo">
  <div class="rot" style="font:800 11px/14px 'Plus Jakarta Sans',sans-serif;letter-spacing:.07em;text-transform:uppercase;margin-bottom:12px">Limitações</div>
  <ul class="lista">
    <li><strong>LLM simulado.</strong> Latência, erros e resolução descrevem o sistema sem o modelo: um piso, não a experiência do usuário.</li>
    <li><strong>Rota única.</strong> Só o especialista educacional foi exercitado; FAQ, analytics, coletas e grafo têm custos de tempo diferentes.</li>
    <li><strong>Uma máquina, uma execução.</strong> Sem repetição nem carga sustentada.</li>
    <li><strong>Resolução e tokens são premissas.</strong> O juiz simulado aprova tudo e o LLM simulado não reporta tokens.</li>
    <li><strong>Infraestrutura e benefício em zero.</strong> Sem eles não há custo total completo nem ROI único.</li>
    <li><strong>Preços e cotação mudam.</strong> Valem na data da consulta.</li>
  </ul>
</div>`,
);

// ═════════════ 12 · PRÓXIMOS PASSOS ═════════════
const proximos = pg(
  "escura",
  `<div class="metade-esc"></div>
${pos(550, 0, arte({ w: 550, h: 850, seed: 23, cols: 12, rows: 18, stops: CEU_LADO, arvores: [{ x: 120, y: 815, s: 70, paleta: PALETA_ROSA }, { x: 420, y: 800, s: 90, paleta: PALETA_VERDE }] }))}
<div class="a" style="left:60px;top:118px;width:440px">
  <h2 class="grande" style="font-size:80px;line-height:74px">Para fechar o que falta medir</h2>
  <p class="lead" style="margin-top:24px">Quatro passos, nesta ordem. Os dois primeiros destravam quase todos os “a medir” deste relatório.</p>
</div>
<div class="vidro" style="left:600px;top:130px;width:440px">
  <h3>Próximos passos</h3>
  <ol class="passos">
    <li>Rodar o benchmark com a DeepSeek (<span class="v">deepseek-flash</span>, sem thinking), em pico e fora do pico, com perguntas de todas as rotas; guardar tokens por resposta, latência por etapa e taxa de erro.</li>
    <li>Informar a infraestrutura semanal para 100 e 1.000 usuários (<span class="v">COST_INFRA_WEEKLY_BRL_100</span> e <span class="v">_1000</span>).</li>
    <li>Definir o benefício por resolução (<span class="v">COST_BENEFIT_PER_RESOLUTION_BRL</span>) e medir as solicitações por usuário por semana com uso real.</li>
    <li>Separar cache hit de cache miss no cálculo do backend: hoje o <span class="v">cost.py</span> usa um único preço de entrada, o que superestima o custo.</li>
  </ol>
</div>
<div class="vidro" style="left:600px;top:500px;width:440px">
  <h3>Referências</h3>
  <ul class="lista">
    <li>DeepSeek, preços: api-docs.deepseek.com/quick_start/pricing</li>
    <li>Cotação: api.frankfurter.dev</li>
    <li>Repositório: <span class="v">docs/SRE_E_CUSTOS.md</span>, <span class="v">docs/PAINEL_DE_CONTROLE.md</span> e <span class="v">observability/</span> (Prometheus e Grafana)</li>
  </ul>
</div>`,
);

const html = `<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>Relatório de observabilidade, custo e ROI · EcoCiente IA</title>
<link rel="stylesheet" href="relatorio.css">
</head>
<body>
${[capa, intro, resumo, custo1, custo2, lat1, lat2, erros, resol, roiPg, metodo, proximos].join("\n")}
</body>
</html>
`;

writeFileSync(join(aqui, "relatorio.html"), html, "utf8");
writeFileSync(
  join(aqui, "dados", "resumo.json"),
  JSON.stringify({ gerado_em: new Date().toISOString(), cotacao: { usdBrl: USD_BRL, data: DATA_COTACAO }, premissas: PREM, bench: { c1: b1, c10: b10 }, chat, entreEtapas, nos, http: snap.http, juiz: snap.juiz, rotas: snap.rotas, custosBackend: custosBackend.amostras, cenarios: cenarios.map((c) => ({ usuarios: c.usuarios, casos: c.casos.map((k) => ({ caso: k.id, ...k.r })) })) }, null, 2),
  "utf8",
);
await vite.close();
console.log(`ok: relatorio.html (${n} páginas) e dados/resumo.json`);
