import "@fontsource-variable/nunito";
import "@fontsource-variable/plus-jakarta-sans";
import "@fontsource-variable/jetbrains-mono";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/painel.css";

import { buscarCotacao, type Cotacao } from "./data/cotacao";
import { Fonte } from "./data/fonte";
import type { Estado } from "./types";
import { renderAgora } from "./ui/agora";
import { iniciarFluxo, renderFluxo } from "./ui/fluxo";
import { iniciarProjecao, renderProjecao } from "./ui/projecao";
import { renderTopo } from "./ui/topo";
import { hora, icone, pintar } from "./ui/util";

const app = document.getElementById("app");
if (!app) throw new Error("#app não encontrado");

app.innerHTML = `
<div class="app">
  <header class="topo">
    <div class="marca">
      <img src="/brand/arvore-creme.svg" alt="" width="60" height="51" />
      <div><h1>Painel de controle</h1><p>EcoCiente IA · ia-eko</p></div>
    </div>
    <div class="estado" id="estado" aria-live="polite"></div>
    <div class="controles">
      <div class="seg" role="radiogroup" aria-label="Origem dos dados" id="modo">
        <button type="button" role="radio" aria-checked="true" data-modo="ao-vivo">Ao vivo</button>
        <button type="button" role="radio" aria-checked="false" data-modo="demo">Demonstração</button>
      </div>
      <button type="button" class="btn-icone" id="pausa" aria-label="Pausar atualização" title="Pausar atualização"></button>
    </div>
  </header>
  <div class="aviso" id="aviso" role="status" hidden></div>
  <section class="faixa" id="faixa" aria-label="Indicadores gerais"></section>
  <nav class="abas" role="tablist" aria-label="Seções do painel">
    <button type="button" role="tab" aria-selected="true" data-aba="agora">Agora</button>
    <button type="button" role="tab" aria-selected="false" data-aba="fluxo">Fluxo</button>
    <button type="button" role="tab" aria-selected="false" data-aba="projecao">Projeção</button>
  </nav>
  <main class="mesa">
    <section class="col col-agora ativa" id="agora" aria-labelledby="t-agora">
      <div class="col-cab"><h2 id="t-agora">Agora</h2></div>
      <div id="agora-corpo" class="col-corpo" style="display:grid;gap:var(--e5)"></div>
    </section>
    <section class="col col-fluxo" id="fluxo" aria-labelledby="t-fluxo">
      <div class="col-cab">
        <h2 id="t-fluxo">Fluxo dos agentes</h2>
        <div class="seg seg--claro" role="radiogroup" aria-label="Janela de tempo" id="janela"></div>
      </div>
      <div id="fluxo-corpo" style="display:grid;gap:var(--e5)"></div>
    </section>
    <section class="col col-projecao" id="projecao" aria-labelledby="t-proj">
      <div class="col-cab"><h2 id="t-proj">Projeção semanal</h2></div>
      <div class="bloco"><h3>Custo, custo por resolução e ROI <span class="mudo" id="proj-resumo"></span></h3><div id="proj-tabela"></div></div>
      <div class="bloco">
        <h3>Premissas editáveis <button type="button" class="btn" id="premissas-restaurar">Restaurar medidos</button></h3>
        <div class="premissas" id="premissas"></div>
      </div>
    </section>
  </main>
  <footer class="rodape">
    <span id="rodape-leitura">Aguardando a primeira leitura…</span>
    <span>Requisições = chamadas HTTP · respostas = /chat concluído · solicitações = projeção semanal · resolvida = aprovada pelo juiz de saída. Infra e benefício são premissas até alguém medir.</span>
  </footer>
</div>`;

const fonte = new Fonte();
let cotacao: Cotacao | null = null;
let estado: Estado | null = null;

function desenhar(): void {
  if (!estado) return;
  const agora = Date.now();
  renderTopo(estado, cotacao, agora);
  renderAgora(estado);
  renderFluxo(estado);
  renderProjecao(estado, cotacao);
  const t = estado.atual?.t;
  pintar(document.getElementById("rodape-leitura"), t ? `Última leitura às ${hora(t)}` : "Aguardando a primeira leitura…");
}

iniciarFluxo(desenhar);
iniciarProjecao();

fonte.assinar((e) => {
  estado = e;
  desenhar();
});

// o relógio do cabeçalho anda mesmo entre duas leituras
window.setInterval(() => {
  if (estado) renderTopo(estado, cotacao, Date.now());
}, 1000);

void buscarCotacao().then((c) => {
  cotacao = c;
  desenhar();
});

// modo ao vivo / demonstração
const modo = document.getElementById("modo");
modo?.addEventListener("click", (ev) => {
  const alvo = (ev.target as HTMLElement).closest<HTMLButtonElement>("button[data-modo]");
  if (!alvo) return;
  modo.querySelectorAll("button").forEach((b) => b.setAttribute("aria-checked", String(b === alvo)));
  fonte.definirModo(alvo.dataset.modo === "demo" ? "demo" : "ao-vivo");
});

// pausa
const pausa = document.getElementById("pausa");
function pintarPausa(): void {
  if (!pausa) return;
  const parado = fonte.estaPausado;
  pausa.innerHTML = icone(parado ? "tocar" : "pausa");
  pausa.setAttribute("aria-label", parado ? "Retomar atualização" : "Pausar atualização");
  pausa.title = parado ? "Retomar atualização" : "Pausar atualização";
}
pausa?.addEventListener("click", () => {
  fonte.pausar(!fonte.estaPausado);
  pintarPausa();
});
pintarPausa();

// abas (telas estreitas): as três colunas viram abas
const abas = document.querySelector(".abas");
abas?.addEventListener("click", (ev) => {
  const alvo = (ev.target as HTMLElement).closest<HTMLButtonElement>("button[data-aba]");
  if (!alvo) return;
  abas.querySelectorAll("button").forEach((b) => b.setAttribute("aria-selected", String(b === alvo)));
  document.querySelectorAll(".col").forEach((c) => c.classList.toggle("ativa", c.id === alvo.dataset.aba));
});

fonte.iniciar();
