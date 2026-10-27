import type { Cotacao } from "../data/cotacao";
import { COTACAO_RESERVA, FATIA_PICO_UNIFORME, MODELO, MODELO_MODO } from "../data/preco";
import { faixaPorSolicitacaoBrl, simular, type EntradaSimulacao } from "../data/simular";
import type { Estado } from "../types";
import { totalRespostas } from "./derivar";
import { brl, decimal, esc, inteiro, percentual, pintar } from "./util";

const AMOSTRAS_MINIMAS = 5;
const CHAVE = "ecociente.premissas";

type Id =
  | "solicitacoes"
  | "tokensEntrada"
  | "tokensSaida"
  | "cacheHit"
  | "fatiaPico"
  | "resolucao"
  | "infra100"
  | "infra1000"
  | "infraLivre"
  | "beneficio"
  | "usuariosLivre";

interface Campo {
  id: Id;
  rotulo: string;
  dica: string;
  min: number;
  max: number;
  step: number;
  /** valor exibido = valor interno x escala (percentuais) */
  escala: number;
  intervalo: boolean;
}

const CAMPOS: Campo[] = [
  { id: "usuariosLivre", rotulo: "Usuários por semana (cenário livre)", dica: "usuários", min: 1, max: 10000, step: 1, escala: 1, intervalo: true },
  { id: "solicitacoes", rotulo: "Solicitações por usuário por semana", dica: "por usuário", min: 0.5, max: 50, step: 0.5, escala: 1, intervalo: true },
  { id: "tokensEntrada", rotulo: "Tokens de entrada por resposta", dica: "tokens", min: 100, max: 20000, step: 50, escala: 1, intervalo: true },
  { id: "tokensSaida", rotulo: "Tokens de saída por resposta", dica: "tokens", min: 50, max: 4000, step: 10, escala: 1, intervalo: true },
  { id: "cacheHit", rotulo: "Entrada servida do cache (cache hit)", dica: "%", min: 0, max: 100, step: 1, escala: 100, intervalo: true },
  { id: "fatiaPico", rotulo: "Tráfego no horário de pico", dica: "%", min: 0, max: 100, step: 1, escala: 100, intervalo: true },
  { id: "resolucao", rotulo: "Respostas resolvidas", dica: "%", min: 0, max: 100, step: 1, escala: 100, intervalo: true },
  { id: "beneficio", rotulo: "Benefício por resolução (R$)", dica: "R$", min: 0, max: 100000, step: 0.1, escala: 1, intervalo: false },
  { id: "infra100", rotulo: "Infra por semana, 100 usuários (R$)", dica: "R$", min: 0, max: 1e9, step: 1, escala: 1, intervalo: false },
  { id: "infra1000", rotulo: "Infra por semana, 1.000 usuários (R$)", dica: "R$", min: 0, max: 1e9, step: 1, escala: 1, intervalo: false },
  { id: "infraLivre", rotulo: "Infra por semana, cenário livre (R$)", dica: "R$", min: 0, max: 1e9, step: 1, escala: 1, intervalo: false },
];

type Origem = "medido" | "demo" | "backend" | "premissa" | "editado";
interface Valores {
  v: Record<Id, number>;
  origem: Record<Id, Origem>;
}

let editados: Partial<Record<Id, number>> = lerEditados();
let ultimoEstado: Estado | null = null;
let ultimaCotacao: Cotacao | null = null;

function lerEditados(): Partial<Record<Id, number>> {
  try {
    const bruto = localStorage.getItem(CHAVE);
    return bruto ? (JSON.parse(bruto) as Partial<Record<Id, number>>) : {};
  } catch {
    return {};
  }
}

function salvarEditados(): void {
  try {
    localStorage.setItem(CHAVE, JSON.stringify(editados));
  } catch {
    /* sem armazenamento: as edições valem só nesta aba */
  }
}

/** Valores padrão: o que foi medido, quando há amostra; senão o que o backend declara; senão uma premissa rotulada. */
function padroes(estado: Estado): Valores {
  const s = estado.atual;
  const c = estado.custos?.premissas;
  const n = s ? totalRespostas(s) : 0;
  const medido = !!s && n >= AMOSTRAS_MINIMAS;
  const tagMedida: Origem = estado.origem === "demo" ? "demo" : "medido";
  const v = {
    usuariosLivre: 250,
    solicitacoes: c?.solicitacoes_por_usuario_semana ?? 5,
    tokensEntrada: medido ? Math.round(s.tokens.entrada / n) : 2400,
    tokensSaida: medido ? Math.round(s.tokens.saida / n) : 380,
    cacheHit: 0.5,
    fatiaPico: FATIA_PICO_UNIFORME,
    resolucao: medido ? s.chats.resolvidas / n : 0.85,
    beneficio: c?.beneficio_por_resolucao_brl ?? 0,
    infra100: c?.infra_semanal_100_brl ?? 0,
    infra1000: c?.infra_semanal_1000_brl ?? 0,
    infraLivre: 0,
  } satisfies Record<Id, number>;
  const origem: Record<Id, Origem> = {
    usuariosLivre: "premissa",
    solicitacoes: c ? "backend" : "premissa",
    tokensEntrada: medido ? tagMedida : "premissa",
    tokensSaida: medido ? tagMedida : "premissa",
    cacheHit: "premissa",
    fatiaPico: "premissa",
    resolucao: medido ? tagMedida : "premissa",
    beneficio: c ? "backend" : "premissa",
    infra100: c ? "backend" : "premissa",
    infra1000: c ? "backend" : "premissa",
    infraLivre: "premissa",
  };
  for (const id of Object.keys(editados) as Id[]) {
    const e = editados[id];
    if (e !== undefined) {
      v[id] = e;
      origem[id] = "editado";
    }
  }
  return { v, origem };
}

export function iniciarProjecao(): void {
  const alvo = document.getElementById("premissas");
  if (!alvo) return;
  alvo.innerHTML = CAMPOS.map((c) => {
    const faixa = c.intervalo
      ? `<input type="range" id="r-${c.id}" min="${c.min}" max="${c.max}" step="${c.step}" aria-label="${esc(c.rotulo)}" />`
      : "";
    return `<div class="campo"><label for="n-${c.id}">${esc(c.rotulo)}<span id="s-${c.id}"></span></label>
      <input type="number" id="n-${c.id}" min="${c.min}" max="${c.max}" step="${c.step}" inputmode="decimal" />${faixa}</div>`;
  }).join("");

  alvo.addEventListener("input", (ev) => {
    const el = ev.target as HTMLInputElement;
    const m = /^[rn]-(.+)$/.exec(el.id);
    if (!m) return;
    const id = m[1] as Id;
    const campo = CAMPOS.find((c) => c.id === id);
    if (!campo) return;
    const bruto = Number(el.value.replace(",", "."));
    if (!Number.isFinite(bruto) || el.value === "") return;
    editados[id] = Math.max(0, bruto / campo.escala);
    salvarEditados();
    redesenhar();
  });

  document.getElementById("premissas-restaurar")?.addEventListener("click", () => {
    editados = {};
    salvarEditados();
    redesenhar();
  });
}

function redesenhar(): void {
  if (ultimoEstado) renderProjecao(ultimoEstado, ultimaCotacao);
}

const ROTULO_SELO: Record<Origem, string> = {
  medido: "medido",
  demo: "demo",
  backend: "do backend",
  premissa: "premissa",
  editado: "editado",
};

function selo(o: Origem): string {
  const classe = o === "medido" ? " selo--medido" : o === "demo" ? " selo--demo" : "";
  return `<span class="selo${classe}">${ROTULO_SELO[o]}</span>`;
}

function sincronizar(val: Valores): void {
  for (const c of CAMPOS) {
    const num = document.getElementById(`n-${c.id}`) as HTMLInputElement | null;
    const faixa = document.getElementById(`r-${c.id}`) as HTMLInputElement | null;
    const exibido = String(Number((val.v[c.id] * c.escala).toFixed(c.escala === 100 ? 1 : 2)));
    if (num && document.activeElement !== num) num.value = exibido;
    if (faixa && document.activeElement !== faixa) faixa.value = exibido;
    const s = document.getElementById(`s-${c.id}`);
    if (s) pintar(s, selo(val.origem[c.id]));
  }
}

export function renderProjecao(estado: Estado, cotacao: Cotacao | null): void {
  ultimoEstado = estado;
  ultimaCotacao = cotacao;
  const val = padroes(estado);
  sincronizar(val);

  const usdBrl = cotacao?.usdBrl ?? COTACAO_RESERVA.usdBrl;
  const v = val.v;
  const base = (usuarios: number, infra: number): EntradaSimulacao => ({
    usuarios,
    solicitacoesPorUsuarioSemana: v.solicitacoes,
    tokensEntradaPorResposta: v.tokensEntrada,
    tokensSaidaPorResposta: v.tokensSaida,
    cacheHit: v.cacheHit,
    fatiaPico: v.fatiaPico,
    resolucao: v.resolucao,
    usdBrl,
    infraSemanalBrl: infra,
    beneficioPorResolucaoBrl: v.beneficio,
  });
  const cenarios = [
    { rot: "100 usuários", e: base(100, v.infra100) },
    { rot: "1.000 usuários", e: base(1000, v.infra1000) },
  ].map((c) => ({ ...c, r: simular(c.e) }));
  const livre = simular(base(v.usuariosLivre, v.infraLivre));
  const faixa = faixaPorSolicitacaoBrl(v.tokensEntrada, v.tokensSaida, usdBrl);

  const linha = (rot: string, f: (i: number) => string, classe = ""): string =>
    `<tr${classe ? ` class="${classe}"` : ""}><td>${rot}</td>${cenarios.map((_, i) => `<td class="valor">${f(i)}</td>`).join("")}</tr>`;

  const semBeneficio = v.beneficio <= 0;
  const roiTxt = (i: number): string => {
    const roi = cenarios[i].r.roi;
    if (semBeneficio) return "—";
    if (roi === null) return "—";
    const pct = `${roi >= 0 ? "+" : ""}${decimal(roi * 100, 0)}%`;
    return `<span class="${roi >= 0 ? "roi-pos" : "roi-neg"}">${pct}</span>`;
  };

  const tabela = `<table class="proj">
    <thead><tr><th scope="col"></th>${cenarios.map((c) => `<th scope="col">${esc(c.rot)}</th>`).join("")}</tr></thead>
    <tbody>
      ${linha("Solicitações por semana", (i) => inteiro(cenarios[i].r.solicitacoesSemana))}
      ${linha("Custo por solicitação (projetado)", (i) => brl(cenarios[i].r.custoPorSolicitacaoBrl))}
      <tr class="faixa-linha"><td>Faixa por solicitação</td><td colspan="2" class="valor">${brl(faixa.melhor)} (melhor) a ${brl(faixa.pior)} (pior)</td></tr>
      ${linha("Custo de API por semana", (i) => brl(cenarios[i].r.custoApiSemanaBrl))}
      ${linha("Infra por semana", (i) => ([v.infra100, v.infra1000][i] > 0 ? brl([v.infra100, v.infra1000][i]) : "não informada"))}
      ${linha("Custo total por semana", (i) => brl(cenarios[i].r.custoTotalSemanaBrl), "destaque")}
      ${linha("Resoluções por semana", (i) => inteiro(cenarios[i].r.resolucoesSemana))}
      ${linha("Custo por resolução", (i) => brl(cenarios[i].r.custoPorResolucaoBrl), "destaque")}
      ${linha("Benefício por semana", (i) => (semBeneficio ? "—" : brl(cenarios[i].r.beneficioSemanaBrl)))}
      ${linha("ROI", roiTxt, "destaque")}
    </tbody></table>
    <p class="livre"><strong>${inteiro(v.usuariosLivre)} usuários (cenário livre):</strong> <span class="valor">${brl(livre.custoTotalSemanaBrl)}</span> por semana · <span class="valor">${brl(livre.custoPorResolucaoBrl)}</span> por resolução${semBeneficio || livre.roi === null ? "" : ` · ROI <span class="valor">${livre.roi >= 0 ? "+" : ""}${decimal(livre.roi * 100, 0)}%</span>`}</p>
    <p class="nota">${semBeneficio ? "ROI e benefício aparecem quando você informa o benefício por resolução. " : ""}Faixa: melhor caso = fora do pico e 90% de cache; pior caso = tudo no pico e sem cache. Preços do ${esc(MODELO)} (${esc(MODELO_MODO)}) a US$ 1 = R$ ${decimal(usdBrl, 4)}.</p>`;

  const c = estado.custos;
  const backend =
    estado.origem === "ao-vivo" && c
      ? `<p class="nota">Conferência com o backend (<span class="valor">/api/v1/costs</span>, preço vindo do ambiente): ${c.cenarios
          .map((x) => `${inteiro(x.usuarios_semanais)} usuários → ${x.custo_total_brl === null ? "sem amostras" : brl(x.custo_total_brl)}`)
          .join(" · ")}. Valor zero significa que <span class="valor">LLM_PRICE_*_PER_1M_BRL</span> está em 0.</p>`
      : "";

  pintar(document.getElementById("proj-tabela"), tabela + backend);
  pintar(
    document.getElementById("proj-resumo"),
    `Resolução: ${percentual(v.resolucao, 0)} das respostas aprovadas pelo juiz. Amostras medidas: ${inteiro(estado.atual ? totalRespostas(estado.atual) : 0)}.`,
  );
}
