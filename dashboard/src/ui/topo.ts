import type { Cotacao } from "../data/cotacao";
import { MODELO, MODELO_MODO } from "../data/preco";
import { quantil } from "../data/quantil";
import type { Estado } from "../types";
import { taxaPorMinuto, totalRespostas } from "./derivar";
import { brl, decimal, duracao, esc, icone, inteiro, percentual, pintar, segundos } from "./util";

const LIMITE_ERRO_ALERTA = 0.05;

export function renderTopo(estado: Estado, cotacao: Cotacao | null, agora: number): void {
  const demo = estado.origem === "demo";
  const atual = estado.atual;

  const origem = demo
    ? `<span class="chip chip--demo" title="Dados simulados, nada aqui foi medido">${icone("demo")} Demonstração · dados simulados</span>`
    : "";

  let saude = "";
  if (!demo && estado.saude) {
    const s = estado.saude.status;
    const txt = s === "ok" ? "API saudável" : s === "degraded" ? "API degradada" : "Saúde indisponível";
    saude = `<span class="chip ${s === "ok" ? "" : "chip--alerta"}">${icone(s === "ok" ? "ok" : "alerta")} ${txt}</span>`;
  }

  const modelo = `<span class="chip">${icone("medidor")} ${esc(MODELO)} · ${esc(MODELO_MODO)}</span>`;
  const cot = cotacao
    ? `<span class="chip" title="Fonte: ${cotacao.fonte === "frankfurter" ? "Frankfurter" : "cotação salva (API indisponível)"}">${icone("moeda")} <span class="valor">US$ 1 = R$ ${decimal(cotacao.usdBrl, 4)}</span> · ${esc(cotacao.data.split("-").reverse().slice(0, 2).join("/"))}${cotacao.fonte === "reserva" ? " (salva)" : ""}</span>`
    : "";

  const desde = !demo && atual?.inicioProcesso ? atual.inicioProcesso : estado.observandoDesde;
  const rotuloTempo = !demo && atual?.inicioProcesso ? "API rodando há" : "Observando há";
  const tempo = `<span class="chip">${icone("relogio")} ${rotuloTempo} <span class="valor">${duracao(agora - desde)}</span></span>`;

  pintar(document.getElementById("estado"), origem + saude + modelo + cot + tempo);

  const aviso = document.getElementById("aviso");
  if (aviso) {
    aviso.hidden = !demo;
    if (demo) {
      const motivo = estado.motivoDemo
        ? `A API não respondeu (${esc(estado.motivoDemo)}). Voltando ao vivo sozinho quando ela responder.`
        : "Modo demonstração escolhido por você.";
      pintar(aviso, `${icone("alerta")} <span><strong>Demonstração:</strong> todos os números abaixo são simulados. ${motivo}</span>`);
    }
  }

  renderFaixa(estado);
}

function renderFaixa(estado: Estado): void {
  const s = estado.atual;
  const demo = estado.origem === "demo";
  const el = document.getElementById("faixa");
  if (!s) {
    pintar(el, principal("Custo por resolução", "—", "conectando à API…") + `<div class="ind-resto"></div>`);
    return;
  }
  const n = totalRespostas(s);
  const taxa = taxaPorMinuto(estado.historico);
  const errosRazao = s.http.total > 0 ? s.http.erros5xx / s.http.total : null;
  const alerta = errosRazao !== null && errosRazao >= LIMITE_ERRO_ALERTA;
  const resolvidaRazao = n > 0 ? s.chats.resolvidas / n : null;
  const p95 = quantil(s.chat, 0.95);
  const p50 = quantil(s.chat, 0.5);
  const porResposta = n > 0 ? s.custoBrl / n : null;
  const porResolucao = s.chats.resolvidas > 0 ? s.custoBrl / s.chats.resolvidas : null;
  const origem = demo ? "simulado" : "medido";

  const estadoErro = alerta
    ? `${icone("erro")} ACIMA DE ${percentual(LIMITE_ERRO_ALERTA, 0)}`
    : `${icone("ok")} dentro do limite de ${percentual(LIMITE_ERRO_ALERTA, 0)}`;

  const html =
    principal(
      "Custo por resolução",
      brl(porResolucao),
      `custo de API ${origem} ÷ respostas aprovadas pelo juiz; a infra entra na projeção`,
    ) +
    `<div class="ind-resto">` +
    linha("Requisições", inteiro(s.http.total), taxa === null ? "calculando…" : `${decimal(taxa, 1)} por min`) +
    linha("Custo até agora", brl(s.custoBrl), porResposta === null ? "sem respostas" : `${brl(porResposta)} por resposta (${origem})`) +
    linha("Respostas resolvidas", percentual(resolvidaRazao), n > 0 ? `${inteiro(s.chats.resolvidas)} de ${inteiro(n)}` : "sem respostas") +
    linha(
      "Índice de erros",
      percentual(errosRazao),
      `${inteiro(s.http.erros5xx)} falhas 5xx de ${inteiro(s.http.total)}`,
      { estado: estadoErro, alerta },
    ) +
    linha("Tempo total p95", segundos(p95), `mediana ${segundos(p50)}`) +
    `</div>`;
  pintar(el, html);
  el?.setAttribute("data-origem", demo ? "demo" : "ao-vivo");
}

function principal(rotulo: string, valor: string, sub: string): string {
  return `<div class="ind ind--principal"><span class="ind-rot">${esc(rotulo)}</span><strong class="valor ind-val">${esc(valor)}</strong><span class="ind-sub">${esc(sub)}</span></div>`;
}

function linha(rotulo: string, valor: string, sub: string, extra?: { estado: string; alerta: boolean }): string {
  return `<div class="ind-linha${extra?.alerta ? " ind-linha--alerta" : ""}"><span class="ind-rot">${esc(rotulo)}</span><span class="valor ind-v">${esc(valor)}</span><span class="ind-sub">${esc(sub)}</span>${extra ? `<span class="ind-estado">${extra.estado}</span>` : ""}</div>`;
}
