import type { Estado } from "../types";
import { DECISAO, JANELAS, visaoFluxo, type Etapa, type JanelaId, type VisaoFluxo } from "./derivar";
import { decimal, esc, icone, inteiro, percentual, pintar, segundos } from "./util";

const CORES_FASE: Record<string, string> = {
  guardrails: "var(--salvia)",
  memoria: "var(--floresta)",
  orquestrador: "var(--azul)",
  especialistas: "var(--verde-profundo)",
  juiz: "var(--rosa)",
  entre: "var(--tinta)",
};

let janela: JanelaId = "5m";

/**
 * Monta a estrutura fixa da coluna uma única vez. O trilho (e seu pulso animado) fica fora das
 * áreas que se redesenham a cada ciclo, senão a animação reiniciaria a cada leitura.
 */
export function iniciarFluxo(aoMudar: () => void): void {
  const corpo = document.getElementById("fluxo-corpo");
  const seg = document.getElementById("janela");
  if (!corpo || !seg) return;

  seg.innerHTML = JANELAS.map(
    (j) => `<button type="button" role="radio" aria-checked="${j.id === janela}" data-janela="${j.id}">${j.rotulo}</button>`,
  ).join("");
  seg.addEventListener("click", (ev) => {
    const alvo = (ev.target as HTMLElement).closest<HTMLButtonElement>("button[data-janela]");
    if (!alvo) return;
    janela = alvo.dataset.janela as JanelaId;
    seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-checked", String(b === alvo)));
    aoMudar();
  });

  corpo.innerHTML = `
    <div class="bloco" id="fluxo-tempos"></div>
    <div class="bloco">
      <h3>Etapas do fluxo <span class="mudo">p95 de cada etapa e o tempo entre elas</span></h3>
      <div class="metro" id="metro">
        <div class="trilho"></div>
        <ol class="estacoes" id="estacoes"></ol>
      </div>
      <div class="legenda-fluxo">
        <span><i style="background:var(--azul)"></i>mediana</span>
        <span><i style="background:color-mix(in srgb, var(--azul) 40%, white)"></i>p95</span>
        <span>${icone("alerta")} maior fatia do tempo</span>
        <span><svg class="losango" viewBox="0 0 12 12" aria-hidden="true"><rect x="2" y="2" width="8" height="8" rx="1.5" transform="rotate(45 6 6)"/></svg> etapa que decide</span>
      </div>
    </div>
    <div class="bloco">
      <h3>Onde o tempo vai <span class="mudo">fatia de cada fase</span></h3>
      <div class="onde" id="fluxo-onde"></div>
    </div>`;
}

export function renderFluxo(estado: Estado): void {
  const v = visaoFluxo(estado, janela);
  if (!v) {
    pintar(document.getElementById("fluxo-tempos"), `<p class="vazio">Aguardando a primeira leitura…</p>`);
    return;
  }
  pintar(document.getElementById("fluxo-tempos"), tempos(v));
  pintar(document.getElementById("estacoes"), estacoes(v));
  pintar(document.getElementById("fluxo-onde"), onde(v));

  const metro = document.getElementById("metro");
  if (metro) {
    const h = estado.historico;
    const chegouTrafego = h.length > 1 && h[h.length - 1].http.total > h[h.length - 2].http.total;
    metro.classList.toggle("com-trafego", chegouTrafego);
    const lista = document.getElementById("estacoes");
    metro.style.setProperty("--altura-trilho", `${Math.max(120, (lista?.clientHeight ?? 640) - 28)}px`);
  }
}

function tempos(v: VisaoFluxo): string {
  const itens: Array<[string, number | null]> = [
    ["mediana", v.p50],
    ["p95", v.p95],
    ["p99", v.p99],
    ["média", v.media],
  ];
  return `<p class="tempos-linha" role="group" aria-label="Tempo total de resposta do /chat"><span class="rot">Tempo total de resposta</span>${itens
    .map(([rot, val]) => `<span><span class="mudo">${rot}</span> <span class="valor">${segundos(val)}</span></span>`)
    .join("")}</p>
  <p class="nota">${inteiro(v.respostas)} ${v.respostas === 1 ? "requisição" : "requisições"} ao /chat na janela. Percentis estimados pelos buckets do Prometheus.</p>`;
}

let ordem = 0;

/** Atraso negativo que mantém a fase do pulso entre redesenhos: a animação não reinicia a cada leitura. */
function atraso(): string {
  const periodo = 3.2;
  const fase = (performance.now() / 1000) % periodo;
  return `${(ordem++ * 0.26 - fase).toFixed(2)}s`;
}

function estacoes(v: VisaoFluxo): string {
  ordem = 0;
  const semDados = v.respostas === 0 && v.antes.every((e) => e.chamadas === 0);
  if (semDados) {
    return `<li class="vazio">Sem chamadas na janela escolhida. Escolha “Desde o início” ou espere chegar tráfego.</li>`;
  }
  return [
    ...v.antes.map((e) => estacao(e, v.maxP95)),
    `<li class="estacao" style="--atraso:${atraso()}"><div class="est-cab"><span class="est-nome">Especialistas</span><span class="est-chamadas mudo">o orquestrador escolhe um</span><span></span></div>${
      v.especialistas.length
        ? `<ul class="especialistas">${v.especialistas.map((e) => espec(e, v.maxP95)).join("")}</ul>`
        : `<p class="vazio">Nenhum especialista chamado na janela.</p>`
    }</li>`,
    ...v.depois.map((e) => estacao(e, v.maxP95)),
    v.entreEtapas === null
      ? ""
      : `<li class="estacao estacao--entre" style="--atraso:${atraso()}"><div class="est-cab"><span class="est-nome">Entre as etapas</span><span class="est-chamadas">fora das etapas do grafo, somados</span><span class="valor est-p95">${segundos(v.entreEtapas)}</span></div><div class="barra" aria-hidden="true"><span class="barra-p50 barra-entre" style="width:${Math.min(100, (v.entreEtapas / v.maxP95) * 100).toFixed(1)}%"></span></div></li>`,
  ].join("");
}

function onde(v: VisaoFluxo): string {
  if (!v.fases.some((f) => f.segundos > 0)) return `<p class="vazio">Sem tempos na janela.</p>`;
  const ativas = v.fases.filter((f) => f.fatia > 0);
  const llm =
    v.fatiaLlm === null
      ? "Sem medição de chamadas ao LLM na janela."
      : `Chamadas ao LLM somam ${percentual(v.fatiaLlm, 0)} do tempo das etapas.`;
  return `<div class="pilha" role="img" aria-label="Divisão do tempo por fase">${ativas
    .map((f) => `<span style="flex:${f.fatia.toFixed(4)};background:${CORES_FASE[f.id]}"></span>`)
    .join("")}</div>
    <ul class="pilha-leg">${v.fases
      .map(
        (f) =>
          `<li><i style="background:${CORES_FASE[f.id]}"></i><span>${esc(f.rotulo)}</span><span class="valor">${percentual(f.fatia, 0)}</span></li>`,
      )
      .join("")}</ul>
    <p class="nota">${llm}</p>`;
}

function larguras(e: Etapa, maxP95: number): { p50: string; p95: string } {
  const p = (x: number | null): string => `${Math.min(100, ((x ?? 0) / maxP95) * 100).toFixed(1)}%`;
  return { p50: p(e.p50), p95: p(e.p95) };
}

function estacao(e: Etapa, maxP95: number): string {
  const l = larguras(e, maxP95);
  return `<li class="estacao${DECISAO.has(e.id) ? " estacao--decisao" : ""}${e.gargalo ? " estacao--gargalo" : ""}${e.chamadas === 0 ? " estacao--ociosa" : ""}" style="--atraso:${atraso()}">
    <div class="est-cab">
      <span class="est-nome">${esc(e.rotulo)}${e.gargalo ? ` <span class="selo-gargalo">${icone("alerta")} maior fatia do tempo</span>` : ""}</span>
      <span class="est-chamadas valor">${inteiro(e.chamadas)} ${e.chamadas === 1 ? "chamada" : "chamadas"}</span>
      <span class="valor est-p95" title="mediana ${segundos(e.p50)}">${segundos(e.p95)}</span>
    </div>
    <div class="barra" aria-hidden="true"><span class="barra-p95" style="width:${l.p95}"></span><span class="barra-p50" style="width:${l.p50}"></span></div>
    ${e.chamadas > 0 ? `<span class="sr-only">mediana ${segundos(e.p50)}, ${percentual(e.fatia, 0)} do tempo das etapas</span>` : ""}
  </li>`;
}

const RAMO = `<svg class="ramo" viewBox="0 0 24 32" aria-hidden="true"><path d="M2 0 V20 Q2 28 10 28 H24"/></svg>`;

function espec(e: Etapa, maxP95: number): string {
  const l = larguras(e, maxP95);
  return `<li class="espec" style="--atraso:${atraso()}">${RAMO}
    <div class="est-cab">
      <span class="est-nome">${esc(e.rotulo)}${e.gargalo ? ` <span class="selo-gargalo">${icone("alerta")} maior fatia</span>` : ""}</span>
      <span class="est-chamadas valor">${inteiro(e.chamadas)}x · ${decimal(e.fatia * 100, 0)}%</span>
      <span class="valor est-p95">${segundos(e.p95)}</span>
    </div>
    <div class="barra" aria-hidden="true"><span class="barra-p95" style="width:${l.p95}"></span><span class="barra-p50" style="width:${l.p50}"></span></div>
  </li>`;
}
