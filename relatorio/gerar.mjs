// Gera relatorio.html a partir das medições em relatorio/dados/ e do mesmo código de cálculo do painel.
// Uso (a partir da raiz do repositório):  node relatorio/gerar.mjs
// Depende de dashboard/node_modules (npm install em dashboard/).
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { arte, PALETA_ROSA, PALETA_VERDE } from "./arte.mjs";

const aqui = dirname(fileURLToPath(import.meta.url));
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
// Mesma medição com a DeepSeek real (deepseek-flash), 28 requisições por cenário.
const d1 = bench("bench-deepseek-c1.txt");
const d10 = bench("bench-deepseek-c10.txt");
const seg = (ms) => (ms / 1000).toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
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
const USD_BRL = 5.4325;
const DATA_COTACAO = "30/10/2026";
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

// ───────── infraestrutura na AWS (Terraform de devops-infra-ecociente) ─────────
// Uma EC2 t3.large com k3s em us-east-1, compartilhada com as APIs Java; preço de lista sob demanda.
const HORAS_MES = 730;
const SEMANAS_MES = HORAS_MES / 168;
const AWS_ITENS = [
  { rot: "EC2 t3.large (2 vCPU, 8 GiB)", conta: "730 h × US$ 0,0832", usdMes: 0.0832 * HORAS_MES },
  { rot: "Disco EBS gp3, 30 GB", conta: "30 GB × US$ 0,08", usdMes: 30 * 0.08 },
  { rot: "IPv4 público (Elastic IP)", conta: "730 h × US$ 0,005", usdMes: 0.005 * HORAS_MES },
];
const awsUsdMes = AWS_ITENS.reduce((a, i) => a + i.usdMes, 0);
const FATIA_IA = 2560 / 8192; // requests.memory do pod da IA ÷ memória da t3.large
const infraIaUsdMes = awsUsdMes * FATIA_IA;
const INFRA_MES_BRL = infraIaUsdMes * USD_BRL;
const INFRA_SEMANA_BRL = INFRA_MES_BRL / SEMANAS_MES;
const BANCOS_PAGOS = [["MongoDB Atlas Flex", 8], ["Redis Cloud Essentials 250 MB", 5], ["Neo4j AuraDB Professional 1 GB", 65]];
const bancosUsdMes = BANCOS_PAGOS.reduce((a, [, v]) => a + v, 0);

// ───────── projeção por condomínio ─────────
const RES = { unidades: 75, moradoresPorUnidade: 2.79, precoUnidade: 4 };
RES.usuarios = RES.unidades * RES.moradoresPorUnidade;
RES.receita = RES.unidades * RES.precoUnidade;
const IND = { condominios: 1029, estoqueM2: 42_500_000, precoM2: 0.5, usuarios: 50, usuariosAlto: 500 };
IND.area = IND.estoqueM2 / IND.condominios;
IND.receita = IND.area * IND.precoM2;
const apiMes = (usuarios, c) => simular(entrada(usuarios, c)).custoApiSemanaBrl * SEMANAS_MES;
const solicitacoesMes = (usuarios) => usuarios * PREM.q * SEMANAS_MES;
const condo = (base) => casos.map((c) => ({ ...c, api: apiMes(base.usuarios, c), alto: base.usuariosAlto ? apiMes(base.usuariosAlto, c) : null }));
const resCasos = condo(RES);
const indCasos = condo(IND);
const resEsp = resCasos[1];
const indEsp = indCasos[1];

// ───────── benefício: tempo do síndico que o chatbot libera ─────────
// Hora: salário médio de síndico (Salário.com.br, CAGED). Dedicação: Datafolha/Superlógica.
const SIND = {
  salario: 3214.86,
  horasSemana: 43,
  fatiaDuvidas: 0.25, // premissa: parte da dedicação gasta respondendo dúvidas de moradores
  fatiaChat: 0.5, // premissa: parte dessas dúvidas que o chatbot resolve
  fatiaProf: 0.46, // Datafolha: síndicos profissionais
  condominiosInfra: 10, // premissa: condomínios que dividem a infra da IA
  morador: { horas: 19, unidades: 103 },
  prof: { horas: 32, unidades: 750 },
};
SIND.hora = SIND.salario / (SIND.horasSemana * SEMANAS_MES);
const perfilSind = (p) => {
  const hSem = (p.horas * RES.unidades) / p.unidades;
  const hMes = hSem * SIND.fatiaDuvidas * SIND.fatiaChat * SEMANAS_MES;
  return { hSem, hMes, benef: hMes * SIND.hora };
};
const sMor = perfilSind(SIND.morador);
const sProf = perfilSind(SIND.prof);
const mix = (k) => SIND.fatiaProf * sProf[k] + (1 - SIND.fatiaProf) * sMor[k];
const sMed = { hSem: mix("hSem"), hMes: mix("hMes"), benef: mix("benef") };
const resolMes = solicitacoesMes(RES.usuarios) * PREM.resolucao;
const custoCondo = resEsp.api + INFRA_MES_BRL / SIND.condominiosInfra;
const roiCondo = (b) => (b - custoCondo) / custoCondo;
const BENEF_RES = sMed.benef / resolMes;
const comBenef = (u) => simular({ ...entrada(u, casos[1]), infraSemanalBrl: INFRA_SEMANA_BRL, beneficioPorResolucaoBrl: BENEF_RES });
const roiBack100 = comBenef(100).roi;
const roiBack1000 = comBenef(1000).roi;
const sinal = (r) => `${r >= 0 ? "+" : "−"}${inteiro(Math.abs(r) * 100)}%`;
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
const ULTIMA = "01/11/2026";
const edu = nos.find((x) => x.id === "educacional");
const somaMedNos = somaNos / chat.n;
const totalRotas = Object.values(snap.rotas).reduce((a, b) => a + b, 0);

// ═════════════ 1 · CAPA ═════════════
const capa = `<section class="pg">
<div class="capa-topo"></div>
<div class="capa-chip">Relatório de SRE · 2026</div>
<img class="capa-logo" src="fontes/logo-verde.svg" alt="EcoCiente">
<div class="capa-titulo">Observabilidade,<br>custo e ROI</div>
<div class="capa-meta"><span><b>EcoCiente IA</b> (ia-eko)</span><span>${ULTIMA}</span><span>deepseek-flash · sem thinking</span><span>US$ 1 = R$ ${fmt(USD_BRL, 4)}</span><span>medições no código 583a8c9</span></div>
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
  <p style="margin-bottom:16px">Cada número traz uma etiqueta de origem. Nenhum valor foi inventado: o que depende de tráfego real com a DeepSeek ou de benefício ainda não foi medido e aparece como tal. A infraestrutura é estimada a partir do Terraform da equipe.</p>
  <ul class="legenda">
    <li>${tag("medido")}<span>saiu de uma execução do sistema, com o método descrito na página ${15}.</span></li>
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
  ${cartao("g-rosa", "Custo estimado", bigBrl(brl(exp100.custoApiSemanaBrl)), "por semana · 100 usuários", `Com 1.000 usuários: <b>${brl(exp1000.custoApiSemanaBrl)}</b>. Caso esperado, só API. Infraestrutura na AWS: <b>${brl(INFRA_SEMANA_BRL)}</b> por semana.`, ["estimado"])}
  ${cartao("g-verde", "Tempo de resposta", `${seg(d10.p95)}<span class="u">s</span>`, "p95 · 10 simultâneas · DeepSeek", `Uma por vez: ${seg(d1.p95)} s. Sem o modelo (LLM simulado), o sistema leva ${fmt(b10.p95, 0)} ms: quase todo o tempo é do modelo.`, ["medido"])}
  ${cartao("g-azul", "Índice de erros", `1<span class="u">de</span>${d1.requisicoes + d10.requisicoes}`, "falhas 5xx · DeepSeek", `A falha foi o Neo4j sem resposta (DNS), não o modelo. Com o LLM simulado: ${snap.http.erros5xx} de ${inteiro(chat.n)}.`, ["medido"])}
  ${cartao("g-rosa", "Custo por resolução", bigBrl(brl(exp100.custoPorResolucaoBrl)), "por resolução · caso esperado", `Só API, com ${pct(PREM.resolucao, 0)} de resolução assumida. Infraestrutura fora da conta.`, ["estimado"])}
  ${cartao("g-verde", "Custo/retorno (ROI)", `${sinal(roiCondo(sMed.benef))}`, "por condomínio · com infra", `Benefício: tempo do síndico liberado, ${brl(sMed.benef)} por mês. Com síndico morador, ${sinal(roiCondo(sMor.benef))}; com profissional, ${sinal(roiCondo(sProf.benef))}.`, ["estimado", "premissa"])}
</div>
<div class="nao">
  <div class="sig"><div class="rot">O que este relatório não afirma</div><p>Três limites que valem para todos os números.</p></div>
  <ul class="lista">
    <li>Que o tempo e o índice de erros com a DeepSeek são firmes: vêm de 28 requisições por cenário, e a API de calendário estava fora do ar.</li>
    <li>Que a taxa de resolução é de ${pct(snap.chats.resolvidas / respostas, 0)}: o juiz simulado aprova tudo. A taxa das contas, ${pct(PREM.resolucao, 0)}, é uma premissa.</li>
    <li>Que o benefício é exato: ele depende de quanto do tempo do síndico vai em dúvidas, que não tem fonte. A infraestrutura vem do Terraform e do preço de lista da AWS.</li>
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
<div class="a c1 l2 sig"><div class="rot">O que isso significa</div><p>Nesta escala a API custa centavos por semana. O que pesa de verdade é a infraestrutura: ${brl(INFRA_SEMANA_BRL)} por semana na AWS (página 11), por causa dos 2,5 GB de memória que a IA reserva para o modelo de embeddings.</p></div>
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
      <tr><th scope="row">Infraestrutura por semana</th><td class="v">${brl(INFRA_SEMANA_BRL)}</td></tr>
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
  <p class="sub"><span class="m">Com a DeepSeek, a mediana é de ${seg(d1.mediana)} s (uma por vez). Sem o modelo, o sistema responde em ${fmt(b1.mediana, 0)} ms.</span></p>
</div>
<div class="stat dupla g-verde">
  <div class="rot">p95 do tempo total · DeepSeek real</div>
  <div class="par"><div class="big">${seg(d1.p95)}<span class="u">s</span></div><div class="cap">uma requisição por vez · ${d1.requisicoes} requisições</div></div>
  <div class="par"><div class="big">${seg(d10.p95)}<span class="u">s</span></div><div class="cap">10 simultâneas · ${d10.requisicoes} requisições</div></div>
</div>
<div class="a c3 l1 corpo">
  <p>Duas medições do sistema real: com a <strong>DeepSeek</strong> (<span class="v">deepseek-flash</span>, sem thinking) e com o <strong>LLM simulado</strong> (<span class="v">LLM_PROVIDER=mock</span>), que responde na hora.</p>
  <p>O mock mostra o custo de tempo do próprio sistema: guardrails, memória, roteamento, juiz e serialização. A diferença para a DeepSeek é o tempo do modelo.</p>
</div>
<div class="a c1 l2 sig"><div class="rot">O que isso significa</div><p>O usuário espera de 6 a 8 s por resposta. O sistema em si gasta ${fmt(b1.mediana, 0)} ms: o gargalo é o modelo.</p></div>
<div class="a c23 l2">
  <table>
    <thead><tr><th></th><th class="v">Média</th><th class="v">Mediana</th><th class="v">p95</th><th class="v">p99</th><th class="v">Erros</th><th class="v">Vazão</th></tr></thead>
    <tbody>
      ${[d1, d10, b1, b10]
        .map(
          (b, i) =>
            `<tr><th scope="row">${i < 2 ? "DeepSeek" : "LLM simulado"} · ${b.concorrencia === 1 ? "uma por vez" : `${b.concorrencia} simultâneas`}</th><td class="v">${fmt(b.media, 0)} ms</td><td class="v">${fmt(b.mediana, 0)} ms</td><td class="v">${fmt(b.p95, 0)} ms</td><td class="v">${fmt(b.p99, 0)} ms</td><td class="v">${fmt(b.erro, 2)}%</td><td class="v">${fmt(b.rps, 1)} req/s</td></tr>`,
        )
        .join("")}
    </tbody>
  </table>
  <p class="nota">Medido pelo cliente, com <span class="v">scripts/benchmark_api.py</span>. Máquina de desenvolvimento (Windows, Python 3.14), API e cliente no mesmo computador. DeepSeek: 28 requisições por cenário, com Qdrant e Postgres ativos; o p99 de 52 s é uma única requisição em que o Neo4j não resolveu o DNS. LLM simulado: 200 por cenário, sem bancos nem rede externa. Uma execução de cada; não é capacidade de produção.</p>
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
  <div class="rot" style="font:800 11px/14px 'Plus Jakarta Sans',sans-serif;letter-spacing:.07em;text-transform:uppercase;margin-bottom:9px">O que muda com a DeepSeek ${tag("medido")}</div>
  <p>No caminho padrão o grafo encadeia o orquestrador, o especialista e o juiz de saída, e cada um faz ao menos uma chamada ao LLM; se o juiz reprova, entram a correção e um novo juízo. FAQ via Qdrant e coletas não passam por juiz LLM.</p>
  <p>O tempo total com a DeepSeek é o do sistema somado às chamadas ao modelo, o gargalo: de 6 a 8 s por resposta.</p>
</div>
<div class="a c3 l2 corpo">
  <div class="rot" style="font:800 11px/14px 'Plus Jakarta Sans',sans-serif;letter-spacing:.07em;text-transform:uppercase;margin-bottom:9px">Como medir</div>
  <p>Rodar o benchmark com <span class="v">LLM_PROVIDER=deepseek</span> e a chave no ambiente (<span class="v">--requests 200</span> para um número mais firme), e ler a latência por etapa e por chamada em <span class="v">ecociente_agent_duration_seconds</span> e <span class="v">ecociente_llm_duration_seconds</span>.</p>
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
  <p class="sub"><span class="m">Com a DeepSeek, 1 falha em ${d1.requisicoes + d10.requisicoes} requisições. Com o LLM simulado, ${snap.http.erros5xx} em ${inteiro(chat.n)}.</span></p>
</div>
<div class="stat g-azul">
  <div class="rot">Falhas 5xx · DeepSeek real</div>
  <div class="big">1<span class="u">de</span>${d1.requisicoes + d10.requisicoes}</div>
  <div class="cap">${pct(1 / (d1.requisicoes + d10.requisicoes), 2)} das requisições</div>
  <div class="ori">${tag("medido")}</div>
</div>
<div class="a c3 l1 corpo">
  <p><strong>Definição:</strong> índice de erros = requisições com falha de servidor (HTTP 5xx ou exceção) ÷ requisições totais.</p>
  <p>Respostas 4xx (cliente), bloqueios do guardrail e recusas por limite de uso não entram: aparecem em contadores próprios.</p>
</div>
<div class="a c1 l2 sig"><div class="rot">O que isso significa</div><p>A única falha não foi do modelo: o agente de grafo não alcançou o Neo4j (DNS) e o erro virou HTTP 500. O sistema não trata a indisponibilidade de uma tool, e o <span class="v">/health</span> marcava o Neo4j como ok.</p></div>
<div class="a c23 l2">
  <table>
    <thead><tr><th>Sinal</th><th class="v">Resultado do teste</th><th class="v">Métrica</th></tr></thead>
    <tbody>
      <tr><th scope="row">Falhas 5xx (LLM simulado)</th><td class="v">${snap.http.erros5xx} de ${inteiro(chat.n)} (${pct(snap.http.erros5xx / chat.n, 2)})</td><td class="v" style="font-size:10px">ecociente_http_request_errors_total</td></tr>
      <tr><th scope="row">Respostas reprovadas pelo juiz</th><td class="v">${inteiro(snap.juiz.reprovadas)} de ${inteiro(snap.juiz.aprovadas + snap.juiz.reprovadas)}</td><td class="v" style="font-size:10px">ecociente_judge_decisions_total</td></tr>
      <tr><th scope="row">Entradas barradas pelo guardrail</th><td class="v">${inteiro(Object.values(snap.guardrail).reduce((a, b) => a + b, 0))}</td><td class="v" style="font-size:10px">ecociente_guardrail_blocked_total</td></tr>
      <tr><th scope="row">Recusadas por limite de uso</th><td class="v">${inteiro(snap.cota)}</td><td class="v" style="font-size:10px">ecociente_quota_rejected_total</td></tr>
    </tbody>
  </table>
  <p class="nota">A carga foi pequena (${inteiro(chat.n)} requisições no mock, 56 na DeepSeek) e sem limites de uso da DeepSeek. Para acompanhar em produção: <span class="v">sum(rate(ecociente_http_request_errors_total[5m])) / sum(rate(ecociente_http_requests_total[5m]))</span>.</p>
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
  <p class="nota">Com ${pct(PREM.resolucao, 0)} de resolução assumida. Não inclui a infraestrutura (página 11): somando ${brl(INFRA_SEMANA_BRL)} por semana, o custo por resolução vai a ${brl((exp100.custoApiSemanaBrl + INFRA_SEMANA_BRL) / exp100.resolucoesSemana)} com 100 usuários e ${brl((exp1000.custoApiSemanaBrl + INFRA_SEMANA_BRL) / exp1000.resolucoesSemana)} com 1.000.</p>
</div>`,
);

// ═════════════ 10 · ROI ═════════════
const roiPg = pg(
  "",
  `${abre(10)}
<div class="a c1 l1">
  <h2>Custo/retorno (ROI) ${tag("estimado")}</h2>
  <p class="sub"><span class="m">Valendo o tempo do síndico, o ROI é de ${sinal(roiBack1000)} com 1.000 usuários</span> e de ${sinal(roiBack100)} com 100.</p>
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
  <p>Benefício estimado na página 14, pelo tempo do síndico: <b>${brl(BENEF_RES)}</b> por resolução. Com 100 usuários a infra fixa pesa mais que o benefício; com 1.000, ela se dilui.</p>
</div>
<div class="a c1 l2 sig"><div class="rot">O que isso significa</div><p>O benefício estimado vem de premissas. A tabela mostra o ROI só com API para outros valores de benefício.</p></div>
<div class="a c23 l2">
  <table>
    <thead><tr><th>Benefício por resolução (hipótese)</th>${hipoteses.map((h) => `<th class="v">${brl(h, 2)}</th>`).join("")}</tr></thead>
    <tbody>
      <tr><th scope="row">ROI com 100 usuários</th>${hipoteses.map((h) => `<td class="v">${roi(100, h) >= 0 ? "+" : ""}${inteiro(roi(100, h) * 100)}%</td>`).join("")}</tr>
      <tr><th scope="row">ROI com 1.000 usuários</th>${hipoteses.map((h) => `<td class="v">${roi(1000, h) >= 0 ? "+" : ""}${inteiro(roi(1000, h) * 100)}%</td>`).join("")}</tr>
    </tbody>
  </table>
  <p class="nota">Os valores do cabeçalho são hipóteses de leitura, não estimativas do grupo. Caso esperado, só custo de API. Os percentuais são altos porque o custo de API por resolução é de frações de centavo; com a infraestrutura da página 11 eles caem bastante, sobretudo com 100 usuários, porque a infra é fixa.</p>
</div>`,
);

// ═════════════ 11 · INFRAESTRUTURA NA AWS ═════════════
const usd = (v) => `US$${NBSP}${fmt(v, 2)}`;
const infraPg = pg(
  "",
  `${abre(12)}
<div class="a c1 l1">
  <h2>Infraestrutura na AWS ${tag("estimado")}</h2>
  <p class="sub"><span class="m">A parte da IA na máquina da AWS custa ${brl(INFRA_SEMANA_BRL)} por semana,</span> com 100 ou com 1.000 usuários.</p>
</div>
<div class="stat g-azul">
  <div class="rot">Parcela da IA · preço de lista</div>
  <div class="big">${bigBrl(brl(INFRA_SEMANA_BRL))}<small>/semana</small></div>
  <div class="cap">${brl(INFRA_MES_BRL)} por mês · ${pct(FATIA_IA, 2)} da máquina</div>
  <div class="ori">${tag("estimado")} ${tag("premissa")}</div>
</div>
<div class="a c3 l1 corpo">
  <p>O Terraform da equipe (<span class="v">devops-infra-ecociente</span>) sobe <strong>uma EC2 t3.large com k3s</strong> em us-east-1, com 30 GB de disco gp3 e um IP elástico. A máquina é dividida com as APIs Java.</p>
  <p>A IA fica com a fatia da memória que reserva: 2.560 MiB de 8.192 MiB. Os bancos (MongoDB Atlas, Redis Cloud, Neo4j Aura e Qdrant Cloud) ficam fora da AWS e entram aqui no plano gratuito.</p>
</div>
<div class="a c1 l2 sig"><div class="rot">O que isso significa</div><p>A infra é custo fixo. Mil usuários fazem ${inteiro(1000 * PREM.q)} perguntas por semana: mesmo concentradas em poucas horas, é menos de uma por vez, e a mesma réplica atende os dois cenários.</p></div>
<div class="a c23 l2">
  <table>
    <thead><tr><th>Item</th><th class="v">Conta</th><th class="v">Por mês</th></tr></thead>
    <tbody>
      ${AWS_ITENS.map((i) => `<tr><th scope="row">${i.rot}</th><td class="v">${i.conta}</td><td class="v">${usd(i.usdMes)}</td></tr>`).join("")}
      <tr class="soma"><th scope="row">Máquina inteira</th><td class="v"></td><td class="v">${usd(awsUsdMes)}</td></tr>
      <tr class="soma"><th scope="row">Parcela da IA</th><td class="v">× ${pct(FATIA_IA, 2)}</td><td class="v">${usd(infraIaUsdMes)} = ${brl(INFRA_MES_BRL)}</td></tr>
    </tbody>
  </table>
  <p class="nota">Preço de lista sob demanda; no AWS Academy a conta é paga com créditos. Mês de 730 h; semana = mês ÷ ${fmt(SEMANAS_MES, 3)}. Nos planos pagos de entrada dos bancos (${BANCOS_PAGOS.map(([nome, v]) => `${nome}, ${usd(v)}`).join("; ")}), somam-se ${usd(bancosUsdMes)} por mês, ou ${brl((bancosUsdMes * USD_BRL) / SEMANAS_MES)} por semana. A IA pode chegar a 3,5 GB dos 8 GB da máquina.</p>
</div>`,
);

// ═════════════ 12–13 · PROJEÇÃO POR CONDOMÍNIO ═════════════
const cabCondo = `<thead><tr><th></th>${casos.map((c) => `<th class="v">${c.rotulo}</th>`).join("")}</tr></thead>`;
const linhaCondo = (rot, lista, f) => `<tr><th scope="row">${rot}</th>${lista.map((c) => `<td class="v">${f(c)}</td>`).join("")}</tr>`;

const residencial = pg(
  "",
  `${abre(13)}
<div class="a c1 l1">
  <h2>Condomínio residencial ${tag("estimado")}</h2>
  <p class="sub"><span class="m">A IA consome ${pct(resEsp.api / RES.receita)} do que um condomínio médio paga,</span> no caso esperado.</p>
</div>
<div class="stat dupla g-verde">
  <div class="rot">Condomínio médio · por mês</div>
  <div class="par"><div class="big">${bigBrl(brl(RES.receita, 0))}<small>/mês</small></div><div class="cap">receita do plano · ${RES.unidades} apartamentos × R$${NBSP}4,00</div></div>
  <div class="par"><div class="big">${bigBrl(brl(resEsp.api))}<small>/mês</small></div><div class="cap">custo de API · ${inteiro(RES.usuarios)} moradores</div></div>
</div>
<div class="a c3 l1 corpo">
  <p><strong>Condomínio médio:</strong> ${RES.unidades} apartamentos (média da cidade de São Paulo, Lello 2025) × ${fmt(RES.moradoresPorUnidade)} moradores por domicílio (IBGE, Censo 2022) = <strong>${inteiro(RES.usuarios)} moradores</strong>.</p>
  <p><strong>Plano Empresarial Residencial:</strong> R$ 4,00 por apartamento por mês. Premissa: todos os moradores usam o chat ${PREM.q} vezes por semana, o que é um teto.</p>
</div>
<div class="a c1 l2 sig"><div class="rot">O que isso significa</div><p>Receita e custo crescem juntos com o número de apartamentos, então a proporção vale de 58 a 118 apartamentos. A infra da IA (${brl(INFRA_MES_BRL)} por mês) cabe na receita de um único condomínio.</p></div>
<div class="a c23 l2">
  <table>${cabCondo}<tbody>
    ${linhaCondo("Solicitações por mês", resCasos, () => inteiro(solicitacoesMes(RES.usuarios)))}
    ${linhaCondo("Custo de API por mês", resCasos, (c) => brl(c.api))}
    ${linhaCondo("Por apartamento", resCasos, (c) => brl(c.api / RES.unidades))}
    ${linhaCondo("Parte da receita do plano", resCasos, (c) => pct(c.api / RES.receita))}
  </tbody></table>
  <p class="nota">${casos.map((c) => `${c.rotulo}: ${c.desc}`).join(". ")}. Com 30% dos moradores usando, os custos de API caem para 30%. A infra fixa equivale a ${pct(INFRA_MES_BRL / RES.receita, 0)} da receita de um condomínio, ou ${pct(INFRA_MES_BRL / (10 * RES.receita))} com 10 condomínios. A receita paga o produto inteiro, não só a IA.</p>
</div>`,
);

const industrial = pg(
  "",
  `${abre(14)}
<div class="a c1 l1">
  <h2>Condomínio industrial ${tag("estimado")}</h2>
  <p class="sub"><span class="m">A IA consome ${pct(indEsp.api / IND.receita, 2)} do que um condomínio logístico médio paga,</span> no caso esperado.</p>
</div>
<div class="stat dupla g-azul">
  <div class="rot">Condomínio médio · por mês</div>
  <div class="par"><div class="big">${bigBrl(brl(IND.receita, 0))}<small>/mês</small></div><div class="cap">receita do plano · ${inteiro(IND.area)} m² × R$ 0,50</div></div>
  <div class="par"><div class="big">${bigBrl(brl(indEsp.api))}<small>/mês</small></div><div class="cap">custo de API · ${IND.usuarios} usuários</div></div>
</div>
<div class="a c3 l1 corpo">
  <p><strong>Condomínio médio:</strong> ${fmt(IND.estoqueM2 / 1e6, 1)} milhões de m² de área locável em ${inteiro(IND.condominios)} condomínios logísticos prontos no Brasil (Buildings, 3º tri. 2025) = <strong>${inteiro(IND.area)} m²</strong> por condomínio.</p>
  <p><strong>Plano Industrial:</strong> R$ 0,50 por m² por mês. Não há fonte para o número de usuários: a premissa é de ${IND.usuarios} pessoas das empresas ocupantes usando o chat ${PREM.q} vezes por semana.</p>
</div>
<div class="a c1 l2 sig"><div class="rot">O que isso significa</div><p>No industrial o preço é por área, não por pessoa. A IA é desprezível frente à receita, mesmo com dez vezes mais usuários.</p></div>
<div class="a c23 l2">
  <table>${cabCondo}<tbody>
    ${linhaCondo("Solicitações por mês", indCasos, () => inteiro(solicitacoesMes(IND.usuarios)))}
    ${linhaCondo("Custo de API por mês", indCasos, (c) => brl(c.api))}
    ${linhaCondo("Parte da receita do plano", indCasos, (c) => pct(c.api / IND.receita, 3))}
    ${linhaCondo(`Parte da receita, ${IND.usuariosAlto} usuários`, indCasos, (c) => pct(c.alto / IND.receita, 2))}
  </tbody></table>
  <p class="nota">A área média mistura todas as classes de galpão; na classe A ela é de ${inteiro(33_700_000 / 625)} m² (625 condomínios). A infra fixa da IA (${brl(INFRA_MES_BRL)} por mês) equivale a ${pct(INFRA_MES_BRL / IND.receita, 2)} da receita de um condomínio.</p>
</div>`,
);

// ═════════════ 14 · TEMPO DO SÍNDICO ═════════════
const linhaSind = (rot, f) => `<tr><th scope="row">${rot}</th>${[sMor, sProf, sMed].map((x) => `<td class="v">${f(x)}</td>`).join("")}</tr>`;
const horas = (h) => `${fmt(h, 1)}${NBSP}h`;
const sindicoPg = pg(
  "",
  `${abre(15)}
<div class="a c1 l1">
  <h2>Tempo do síndico ${tag("estimado")}</h2>
  <p class="sub"><span class="m">O chatbot devolve ao síndico ${horas(sMed.hMes)} por mês por condomínio,</span> ou ${brl(sMed.benef)}, na média.</p>
</div>
<div class="stat dupla g-verde">
  <div class="rot">Benefício por condomínio · por mês</div>
  <div class="par"><div class="big">${bigBrl(brl(sMor.benef))}<small>/mês</small></div><div class="cap">síndico morador · ${horas(sMor.hMes)} liberadas</div></div>
  <div class="par"><div class="big">${bigBrl(brl(sProf.benef))}<small>/mês</small></div><div class="cap">síndico profissional · ${horas(sProf.hMes)} liberadas</div></div>
</div>
<div class="a c3 l1 corpo">
  <p><strong>Hora do síndico:</strong> R$ 3.214,86 por mês ÷ (43 h × ${fmt(SEMANAS_MES, 3)} semanas) = <strong>${brl(SIND.hora)}</strong> (Salário.com.br, CAGED). <strong>Dedicação</strong> (Datafolha): o morador dá 19 h por semana a 103 unidades e o profissional, 32 h a 750. Para ${RES.unidades} apartamentos: ${horas(sMor.hSem)} e ${horas(sProf.hSem)} por semana.</p>
  <p>${tag("premissa")} ${pct(SIND.fatiaDuvidas, 0)} desse tempo vai em responder dúvidas, e o chatbot absorve ${pct(SIND.fatiaChat, 0)} delas.</p>
</div>
<div class="a c1 l2 sig"><div class="rot">O que isso significa</div><p>Com síndico morador a IA se paga com folga. O profissional passa tão pouco tempo em cada condomínio que o benefício fica perto do custo.</p></div>
<div class="a c23 l2">
  <table>
    <thead><tr><th></th><th class="v">Morador</th><th class="v">Profissional</th><th class="v">Média (${pct(SIND.fatiaProf, 0)} prof.)</th></tr></thead>
    <tbody>
      ${linhaSind("Horas por semana no condomínio", (x) => horas(x.hSem))}
      ${linhaSind("Horas liberadas por mês", (x) => horas(x.hMes))}
      ${linhaSind("Benefício por mês", (x) => brl(x.benef))}
      ${linhaSind("ROI por condomínio", (x) => sinal(roiCondo(x.benef)))}
      ${linhaSind("Benefício por resolução", (x) => brl(x.benef / resolMes))}
    </tbody>
  </table>
  <p class="nota">Custo da IA por condomínio: ${brl(resEsp.api)} de API (caso esperado, página 12) + infra dividida por ${SIND.condominiosInfra} condomínios = ${brl(custoCondo)} por mês. ${inteiro(resolMes)} resoluções por mês. Sem encargos trabalhistas, então a hora real custa mais. A média por resolução, ${brl(BENEF_RES)}, é a que está no <span class="v">.env</span>.</p>
</div>`,
);

// ═════════════ 15 · MÉTODO E LIMITAÇÕES ═════════════
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
    <li><strong>Amostra pequena com a DeepSeek.</strong> 28 requisições por cenário: p95 e p99 são indicativos. Os tempos por etapa, a resolução e os tokens vêm da rodada com o LLM simulado.</li>
    <li><strong>Rota única.</strong> Só o especialista educacional foi exercitado; FAQ, analytics, coletas e grafo têm custos de tempo diferentes.</li>
    <li><strong>Uma máquina, uma execução.</strong> Sem repetição nem carga sustentada.</li>
    <li><strong>Resolução e tokens são premissas.</strong> O juiz simulado aprova tudo e o LLM simulado não reporta tokens.</li>
    <li><strong>Infraestrutura e benefício em zero.</strong> Sem eles não há custo total completo nem ROI único.</li>
    <li><strong>Preços e cotação mudam.</strong> Valem na data da consulta.</li>
    <li><strong>Código mudou depois da medição.</strong> O thinking da DeepSeek só passou a ser desligado no código em 01/11/2026, e a base de conhecimento passou para o Qdrant. A rodada com a DeepSeek é posterior; as demais medições, não.</li>
  </ul>
</div>`,
);

// ═════════════ 16 · FONTES ═════════════
const FONTES = [
  ["IBGE, Censo Demográfico 2022: média de 2,79 moradores por domicílio ocupado (divulgado em 28/06/2023).", "agenciadenoticias.ibge.gov.br/agencia-noticias/2012-agencia-noticias/noticias/37238-pais-tem-90-milhoes-de-domicilios-34-a-mais-que-em-2010"],
  ["Lello, Mapa dos Condomínios 2025, via Jovem Pan (27/04/2025): 31,1 mil condomínios na cidade de São Paulo, 75 apartamentos em média, de 58 (zona sul) a 118 (zona norte).", "jovempan.com.br/noticias/brasil/zona-sul-concentra-maior-numero-de-condominios-em-sao-paulo-aponta-levantamento.html"],
  ["Buildings (06/10/2025): 1.029 condomínios logísticos prontos e 42,5 milhões de m² de área locável no Brasil; classe A, 33,7 milhões de m² em 625.", "revista.buildings.com.br/brasil-tem-57-milhoes-de-m²-em-construcao-logistica"],
  ["Salário.com.br (CAGED, atualizado em 08/10/2026): síndico de edifícios (CBO 510110), média de R$ 3.214,86 por mês para 43 h semanais.", "salario.com.br/profissao/sindico-edificios-cbo-510110"],
  ["Datafolha para o Grupo Superlógica, Perfil do Síndico Brasileiro (via Revista Meu Condomínio, 04/12/2025): 46% profissionais; profissional, 8 condomínios, 750 unidades, 32 h por semana; morador, 103 unidades, 19 h por semana.", "revistameucondominio.com.br/pesquisa-aponta-que-46-dos-sindicos-no-brasil-ja-atuam-de-forma-profissional"],
  ["EcoCiente, tabela de planos: Empresarial Residencial R$ 4,00 por unidade por mês; Industrial R$ 0,50 por m² por mês.", null],
  [`DeepSeek, preços do deepseek-flash (consultados em ${ULTIMA}).`, "api-docs.deepseek.com/quick_start/pricing"],
  [`Frankfurter, cotação de ${DATA_COTACAO}: US$ 1 = R$ ${fmt(USD_BRL, 4)}.`, "api.frankfurter.dev/v1/latest?base=USD&symbols=BRL"],
  ["Terraform da equipe de infraestrutura: EC2 t3.large, 30 GB gp3, Elastic IP, us-east-1.", "github.com/EcoCiente-Instituto-J-F/devops-infra-ecociente (terraform/aws)"],
  ["AWS EC2 sob demanda: t3.large em us-east-1, US$ 0,0832 por hora (conferido em aws-pricing.com, 31/10/2026).", "aws.amazon.com/ec2/pricing/on-demand"],
  ["AWS EBS: gp3 em us-east-1, US$ 0,08 por GB-mês.", "aws.amazon.com/ebs/pricing"],
  ["AWS VPC: IPv4 público, inclusive Elastic IP, US$ 0,005 por hora.", "aws.amazon.com/vpc/pricing"],
  ["MongoDB Atlas Flex: de US$ 8 a US$ 30 por mês.", "mongodb.com/docs/atlas/billing/atlas-flex-costs"],
  ["Redis Cloud Essentials 250 MB: US$ 5 por mês (valor de marketplace; confirmar na calculadora).", "redis.io/pricing"],
  ["Neo4j AuraDB Professional: US$ 65 por GB por mês, mínimo de 1 GB.", "neo4j.com/pricing"],
  ["Qdrant Cloud: plano gratuito com 1 GB de RAM e 4 GB de disco; pago por uso.", "qdrant.tech/pricing"],
];
const itemFonte = ([texto, url]) => `<li>${texto}${url ? `<br><span class="v" style="font-size:9.5px;font-weight:500;word-break:break-all">${url}</span>` : ""}</li>`;
const fontes = pg(
  "",
  `${abre(24, true, 850)}
<div class="a c1 l1"><div class="grande" style="font-size:72px;line-height:66px">Fontes</div><p class="sub" style="margin-top:18px"><span class="m">De onde vem cada número externo.</span></p></div>
<div class="a c2 l1 corpo" style="font-size:11.5px;line-height:15px"><ul class="lista">${FONTES.slice(0, 7).map(itemFonte).join("")}</ul></div>
<div class="a c3 l1 corpo" style="font-size:11.5px;line-height:15px"><ul class="lista">${FONTES.slice(7).map(itemFonte).join("")}</ul></div>`,
);

const html = `<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>Relatório de observabilidade, custo e ROI · EcoCiente IA</title>
<link rel="stylesheet" href="relatorio.css">
</head>
<body>
${[capa, intro, resumo, custo1, custo2, lat1, lat2, erros, resol, roiPg, infraPg, residencial, industrial, sindicoPg, metodo, fontes].join("\n")}
</body>
</html>
`;

writeFileSync(join(aqui, "relatorio.html"), html, "utf8");
writeFileSync(
  join(aqui, "dados", "resumo.json"),
  JSON.stringify({ gerado_em: new Date().toISOString(), cotacao: { usdBrl: USD_BRL, data: DATA_COTACAO }, premissas: PREM, bench: { c1: b1, c10: b10 }, benchDeepseek: { c1: d1, c10: d10 }, chat, entreEtapas, nos, http: snap.http, juiz: snap.juiz, rotas: snap.rotas, custosBackend: custosBackend.amostras, sindico: { ...SIND, morador: { ...SIND.morador, ...sMor }, prof: { ...SIND.prof, ...sProf }, media: sMed, custoCondo, beneficioPorResolucao: BENEF_RES, roiBackend: { u100: roiBack100, u1000: roiBack1000 } }, infra: { awsUsdMes, fatiaIa: FATIA_IA, mesBrl: INFRA_MES_BRL, semanaBrl: INFRA_SEMANA_BRL, bancosPagosUsdMes: bancosUsdMes }, condominios: { residencial: { ...RES, casos: resCasos.map((c) => ({ caso: c.id, apiMesBrl: c.api })) }, industrial: { ...IND, casos: indCasos.map((c) => ({ caso: c.id, apiMesBrl: c.api, apiMesBrlAlto: c.alto })) } }, cenarios: cenarios.map((c) => ({ usuarios: c.usuarios, casos: c.casos.map((k) => ({ caso: k.id, ...k.r })) })) }, null, 2),
  "utf8",
);
await vite.close();
console.log(`ok: relatorio.html (${n} páginas) e dados/resumo.json`);
