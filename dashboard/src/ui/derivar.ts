import { baseDaJanela, diferenca, media, quantil } from "../data/quantil";
import type { Estado, Hist, Snapshot } from "../types";

export const JANELAS = [
  { id: "1m", rotulo: "1 min", ms: 60_000 },
  { id: "5m", rotulo: "5 min", ms: 300_000 },
  { id: "tudo", rotulo: "Desde o início", ms: null },
] as const;
export type JanelaId = (typeof JANELAS)[number]["id"];

export const ROTULO_ETAPA: Record<string, string> = {
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

export const ESPECIALISTAS = ["faq", "analytics", "educacional", "coletas", "grafo"] as const;
const ANTES = ["guardrail_entrada", "memoria", "verificar_compactacao", "resumir_memoria", "orquestrador"];
const DEPOIS = ["juiz_saida", "correcao", "guardrail_saida"];
/** etapas que decidem (aprovam, roteiam ou barram): desenhadas com marcador em losango */
export const DECISAO = new Set(["orquestrador", "juiz_saida", "guardrail_entrada", "guardrail_saida"]);
/** etapas que só aparecem quando aconteceram ao menos uma vez na janela */
const OPCIONAIS = new Set(["resumir_memoria", "correcao"]);

export interface Etapa {
  id: string;
  rotulo: string;
  chamadas: number;
  p50: number | null;
  p95: number | null;
  total: number;
  fatia: number;
  gargalo: boolean;
}

export interface VisaoFluxo {
  janela: JanelaId;
  respostas: number;
  p50: number | null;
  p95: number | null;
  p99: number | null;
  media: number | null;
  antes: Etapa[];
  especialistas: Etapa[];
  depois: Etapa[];
  entreEtapas: number | null;
  maxP95: number;
  fases: Array<{ id: string; rotulo: string; segundos: number; fatia: number }>;
  fatiaLlm: number | null;
}

function janelaMs(id: JanelaId): number | null {
  return JANELAS.find((j) => j.id === id)?.ms ?? null;
}

function histJanela(atual: Hist | undefined, base: Hist | undefined): Hist | undefined {
  return atual ? diferenca(atual, base) : undefined;
}

export function visaoFluxo(estado: Estado, janela: JanelaId): VisaoFluxo | null {
  const atual = estado.atual;
  if (!atual) return null;
  const base = baseDaJanela(estado.historico, atual, janelaMs(janela));

  const chat = histJanela(atual.chat, base?.chat) ?? { buckets: [], sum: 0, count: 0 };
  const nomes = new Set<string>([...ANTES, ...ESPECIALISTAS, ...DEPOIS, ...Object.keys(atual.agentes)]);
  const brutas = new Map<string, Hist>();
  for (const nome of nomes) {
    const h = histJanela(atual.agentes[nome], base?.agentes[nome]);
    if (h) brutas.set(nome, h);
  }
  const somaEtapas = [...brutas.values()].reduce((a, h) => a + h.sum, 0);
  const maiorSoma = Math.max(0, ...[...brutas.values()].map((h) => h.sum));

  const montar = (id: string): Etapa => {
    const h = brutas.get(id);
    return {
      id,
      rotulo: ROTULO_ETAPA[id] ?? id,
      chamadas: h?.count ?? 0,
      p50: quantil(h, 0.5),
      p95: quantil(h, 0.95),
      total: h?.sum ?? 0,
      fatia: somaEtapas > 0 && h ? h.sum / somaEtapas : 0,
      gargalo: !!h && h.sum > 0 && h.sum === maiorSoma,
    };
  };
  const visivel = (id: string): boolean => !OPCIONAIS.has(id) || (brutas.get(id)?.count ?? 0) > 0;

  const antes = ANTES.filter(visivel).map(montar);
  const depois = DEPOIS.filter(visivel).map(montar);
  const conhecidos = new Set<string>([...ANTES, ...ESPECIALISTAS, ...DEPOIS]);
  const extras = [...nomes].filter((n) => !conhecidos.has(n) && brutas.has(n)).sort();
  const especialistas = [...ESPECIALISTAS, ...extras].map(montar).filter((e) => e.chamadas > 0);

  const todas = [...antes, ...especialistas, ...depois];
  const maxP95 = Math.max(0.001, ...todas.map((e) => e.p95 ?? 0));

  const respostas = chat.count;
  // tempo gasto fora dos nós do grafo (serialização, fila, rede): total médio menos soma das etapas por resposta
  const entre = respostas > 0 ? Math.max(0, chat.sum / respostas - somaEtapas / respostas) : null;

  const soma = (ids: string[]): number => ids.reduce((a, id) => a + (brutas.get(id)?.sum ?? 0), 0);
  const fasesBrutas = [
    { id: "guardrails", rotulo: "Guardrails", segundos: soma(["guardrail_entrada", "guardrail_saida"]) },
    { id: "memoria", rotulo: "Memória", segundos: soma(["memoria", "verificar_compactacao", "resumir_memoria"]) },
    { id: "orquestrador", rotulo: "Orquestrador", segundos: soma(["orquestrador"]) },
    { id: "especialistas", rotulo: "Especialistas", segundos: soma([...ESPECIALISTAS, ...extras]) },
    { id: "juiz", rotulo: "Juiz e correção", segundos: soma(["juiz_saida", "correcao"]) },
    { id: "entre", rotulo: "Entre etapas", segundos: respostas > 0 ? (entre ?? 0) * respostas : 0 },
  ];
  const totalFases = fasesBrutas.reduce((a, f) => a + f.segundos, 0);
  const fases = fasesBrutas.map((f) => ({ ...f, fatia: totalFases > 0 ? f.segundos / totalFases : 0 }));

  const somaLlm = Object.keys(atual.llm).reduce(
    (a, k) => a + (histJanela(atual.llm[k], base?.llm[k])?.sum ?? 0),
    0,
  );

  return {
    janela,
    respostas,
    p50: quantil(chat, 0.5),
    p95: quantil(chat, 0.95),
    p99: quantil(chat, 0.99),
    media: media(chat),
    antes,
    especialistas,
    depois,
    entreEtapas: entre,
    maxP95,
    fases,
    fatiaLlm: somaEtapas > 0 && somaLlm > 0 ? Math.min(1, somaLlm / somaEtapas) : null,
  };
}

/** Requisições por minuto na última janela de 60 s (ou no trecho disponível, se o histórico for menor). */
export function taxaPorMinuto(historico: Snapshot[]): number | null {
  if (historico.length < 2) return null;
  const fim = historico[historico.length - 1];
  const ini = historico.find((s) => s.t >= fim.t - 60_000) ?? historico[0];
  const dt = (fim.t - ini.t) / 60_000;
  if (dt <= 0) return null;
  return Math.max(0, fim.http.total - ini.http.total) / dt;
}

export interface BarraReq {
  t: number;
  total: number;
  erros: number;
}

/** Requisições e falhas 5xx por faixa de 10 s nos últimos 5 min, em ordem cronológica. */
export function serieRequisicoes(historico: Snapshot[], faixas = 30, passoMs = 10_000): BarraReq[] {
  if (historico.length < 2) return [];
  const fim = historico[historico.length - 1].t;
  const saida: BarraReq[] = Array.from({ length: faixas }, (_, i) => ({
    t: fim - (faixas - 1 - i) * passoMs,
    total: 0,
    erros: 0,
  }));
  for (let i = 1; i < historico.length; i++) {
    const a = historico[i - 1];
    const b = historico[i];
    const idx = faixas - 1 - Math.floor((fim - b.t) / passoMs);
    if (idx < 0 || idx >= faixas) continue;
    saida[idx].total += Math.max(0, b.http.total - a.http.total);
    saida[idx].erros += Math.max(0, b.http.erros5xx - a.http.erros5xx);
  }
  return saida;
}

export function totalRespostas(s: Snapshot): number {
  return s.chats.resolvidas + s.chats.naoResolvidas;
}
