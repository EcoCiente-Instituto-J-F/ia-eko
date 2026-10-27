import type { Estado, Snapshot } from "../types";
import { ROTULO_ETAPA, serieRequisicoes, taxaPorMinuto, totalRespostas } from "./derivar";
import { decimal, esc, hora, icone, inteiro, percentual, pintar } from "./util";

interface Evento {
  t: number;
  texto: string;
  erro: boolean;
  icone: Parameters<typeof icone>[0];
}

const MAX_EVENTOS = 40;
let eventos: Evento[] = [];
let ultima: Snapshot | null = null;
let origemAnterior: string | null = null;

/** O backend não expõe uma lista de requisições: os eventos são a diferença entre duas leituras de /metrics. */
function atualizarEventos(estado: Estado): void {
  const s = estado.atual;
  if (!s) return;
  if (origemAnterior !== estado.origem) {
    eventos = [];
    ultima = null;
    origemAnterior = estado.origem;
  }
  if (ultima && s.t > ultima.t) {
    const novos: Evento[] = [];
    const respostas = totalRespostas(s) - totalRespostas(ultima);
    const resolvidas = s.chats.resolvidas - ultima.chats.resolvidas;
    const erros = s.http.erros5xx - ultima.http.erros5xx;
    const reprov = s.juiz.reprovadas - ultima.juiz.reprovadas;
    const cota = s.cota - ultima.cota;

    if (s.http.total < ultima.http.total) {
      novos.push({ t: s.t, texto: "API reiniciou: os contadores voltaram a zero", erro: true, icone: "alerta" });
    } else {
      if (respostas > 0) {
        novos.push({
          t: s.t,
          texto: `${respostas} ${respostas === 1 ? "resposta" : "respostas"} (${resolvidas} resolvida${resolvidas === 1 ? "" : "s"})`,
          erro: false,
          icone: "ok",
        });
      }
      if (erros > 0) novos.push({ t: s.t, texto: `${erros} ${erros === 1 ? "falha" : "falhas"} 5xx`, erro: true, icone: "erro" });
      if (reprov > 0) novos.push({ t: s.t, texto: `Juiz reprovou ${reprov} ${reprov === 1 ? "resposta" : "respostas"}`, erro: false, icone: "escudo" });
      for (const [motivo, n] of Object.entries(s.guardrail)) {
        const d = n - (ultima.guardrail[motivo] ?? 0);
        if (d > 0) novos.push({ t: s.t, texto: `Guardrail barrou ${d} (${motivo.replace(/_/g, " ")})`, erro: false, icone: "escudo" });
      }
      if (cota > 0) novos.push({ t: s.t, texto: `${cota} recusada${cota === 1 ? "" : "s"} por limite de uso`, erro: false, icone: "alerta" });
    }
    eventos = [...novos.reverse(), ...eventos].slice(0, MAX_EVENTOS);
  }
  ultima = s;
}

export function renderAgora(estado: Estado): void {
  atualizarEventos(estado);
  const s = estado.atual;
  if (!s) {
    pintar(document.getElementById("agora-corpo"), `<p class="vazio">Aguardando a primeira leitura…</p>`);
    return;
  }
  const taxa = taxaPorMinuto(estado.historico);
  const serie = serieRequisicoes(estado.historico);

  const html = `
    <div class="bloco">
      <h3>Requisições por minuto <span class="mudo">últimos 5 min</span></h3>
      <div class="taxa"><span class="valor">${taxa === null ? "—" : decimal(taxa, 1)}</span><span class="mudo">por minuto agora</span></div>
      ${grafico(serie)}
    </div>
    <div class="bloco">
      <h3>Eventos <span class="mudo">derivados das métricas</span></h3>
      ${
        eventos.length
          ? `<ul class="eventos">${eventos
              .map(
                (e) =>
                  `<li class="evento${e.erro ? " evento--erro" : ""}"><time>${hora(e.t)}</time>${icone(e.icone)}<span>${esc(e.texto)}</span></li>`,
              )
              .join("")}</ul>`
          : `<p class="vazio">Nenhum evento ainda. Eles aparecem quando chegarem requisições.</p>`
      }
    </div>
    <div class="bloco">
      <h3>Rotas do orquestrador <span class="mudo">desde o início</span></h3>
      ${rotas(s)}
    </div>
    <div class="bloco">
      <h3>Qualidade e barreiras <span class="mudo">desde o início</span></h3>
      ${barreiras(s)}
    </div>`;
  pintar(document.getElementById("agora-corpo"), html);
}

function grafico(serie: ReturnType<typeof serieRequisicoes>): string {
  if (!serie.length) return `<p class="vazio">Coletando pontos para o gráfico…</p>`;
  const W = 300;
  const H = 84;
  const base = 70;
  const topo = 8;
  const passo = W / serie.length;
  const maximo = Math.max(1, ...serie.map((b) => b.total));
  const barras = serie
    .map((b, i) => {
      const alturaTotal = ((b.total / maximo) * (base - topo)) | 0;
      const alturaErro = b.erros > 0 ? Math.max(2, ((b.erros / maximo) * (base - topo)) | 0) : 0;
      const x = (i * passo + 1).toFixed(1);
      const w = (passo - 2).toFixed(1);
      const total = b.total > 0 ? `<rect class="barra-req" x="${x}" y="${base - alturaTotal}" width="${w}" height="${Math.max(1, alturaTotal)}" rx="1"/>` : "";
      const erro = alturaErro ? `<rect class="barra-req barra-req--erro" x="${x}" y="${base - alturaErro}" width="${w}" height="${alturaErro}" rx="1"/>` : "";
      return total + erro;
    })
    .join("");
  const rotMax = decimal(maximo * 6, 0);
  return `<svg class="grafico" viewBox="0 0 ${W} ${H}" role="img" aria-label="Requisições por minuto nos últimos 5 minutos, pico de ${rotMax} por minuto">
    <line class="eixo" x1="0" y1="${base}" x2="${W}" y2="${base}"/>
    ${barras}
    <text x="0" y="${H - 2}">-5 min</text>
    <text x="${W}" y="${H - 2}" text-anchor="end">agora</text>
    <text x="${W}" y="9" text-anchor="end">pico ${rotMax}/min</text>
  </svg>`;
}

function rotas(s: Snapshot): string {
  const entradas = Object.entries(s.rotas).sort((a, b) => b[1] - a[1]);
  const total = entradas.reduce((a, [, n]) => a + n, 0);
  if (!total) return `<p class="vazio">Sem decisões do orquestrador ainda.</p>`;
  const maior = entradas[0][1];
  return entradas
    .map(([id, n]) => {
      const rot = ROTULO_ETAPA[id] ?? id;
      return `<div class="rota"><span>${esc(rot)}</span><div class="trilha"><span style="width:${((n / maior) * 100).toFixed(1)}%"></span></div><span class="valor">${inteiro(n)} · ${percentual(n / total, 0)}</span></div>`;
    })
    .join("");
}

function barreiras(s: Snapshot): string {
  const juizTotal = s.juiz.aprovadas + s.juiz.reprovadas;
  const bloqueios = Object.values(s.guardrail).reduce((a, n) => a + n, 0);
  const linha = (rot: string, val: string): string => `<tr><td>${rot}</td><td class="valor">${val}</td></tr>`;
  return `<table class="tabela-mini"><tbody>
    ${linha("Juiz de saída reprovou", juizTotal ? `${inteiro(s.juiz.reprovadas)} de ${inteiro(juizTotal)} · ${percentual(s.juiz.reprovadas / juizTotal)}` : "—")}
    ${linha("Guardrail bloqueou entradas", inteiro(bloqueios))}
    ${linha("Recusadas por limite de uso", inteiro(s.cota))}
    ${linha("Tokens: entrada / saída", `${inteiro(s.tokens.entrada)} / ${inteiro(s.tokens.saida)}`)}
  </tbody></table>`;
}
