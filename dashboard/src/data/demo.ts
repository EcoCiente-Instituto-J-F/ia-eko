import type { CustosApi, Hist, Saude, Snapshot } from "../types";
import { custoRespostaUsd, simular } from "./simular";
import { COTACAO_RESERVA } from "./preco";

/**
 * Gerador de dados de DEMONSTRAÇÃO. Nada aqui foi medido: as latências, taxas e tokens abaixo são
 * valores plausíveis inventados só para o painel ter o que mostrar quando a API está fora do ar.
 * A interface rotula tudo que vem daqui como "demonstração".
 */

const LIMITES = [0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1, 2.5, 5, 7.5, 10, Infinity];
const ESPECIALISTAS = ["faq", "analytics", "educacional", "coletas", "grafo"] as const;
const PESOS_ROTA = [0.42, 0.18, 0.22, 0.1, 0.08];

/** Mediana (s) de cada etapa na demonstração. */
const MEDIANA: Record<string, number> = {
  guardrail_entrada: 0.02,
  memoria: 0.06,
  verificar_compactacao: 0.01,
  resumir_memoria: 1.1,
  orquestrador: 0.85,
  faq: 1.5,
  analytics: 2.1,
  educacional: 1.7,
  coletas: 0.9,
  grafo: 1.9,
  juiz_saida: 0.8,
  correcao: 1.4,
  guardrail_saida: 0.02,
};

const vazio = (): Hist => ({ buckets: LIMITES.map((le) => [le, 0] as [number, number]), sum: 0, count: 0 });

function observar(h: Hist, valor: number): void {
  for (const b of h.buckets) if (valor <= b[0]) b[1] += 1;
  h.sum += valor;
  h.count += 1;
}

function copiar(h: Hist): Hist {
  return { buckets: h.buckets.map(([le, n]) => [le, n] as [number, number]), sum: h.sum, count: h.count };
}

/** Número pseudoaleatório determinístico para a demonstração ser reproduzível. */
function criarAleatorio(semente: number): () => number {
  let s = semente >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export class GeradorDemo {
  private rnd = criarAleatorio(2610);
  private estado: Snapshot;
  private inicio: number;

  constructor(inicio: number) {
    this.inicio = inicio;
    this.estado = {
      t: inicio,
      inicioProcesso: inicio,
      http: { total: 0, erros5xx: 0 },
      chats: { resolvidas: 0, naoResolvidas: 0 },
      custoBrl: 0,
      tokens: { entrada: 0, saida: 0 },
      chat: vazio(),
      agentes: {},
      llm: {},
      rotas: {},
      juiz: { aprovadas: 0, reprovadas: 0 },
      guardrail: {},
      cota: 0,
    };
  }

  private lognormal(mediana: number): number {
    const u = Math.max(this.rnd(), 1e-9);
    const v = this.rnd();
    const z = Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
    return mediana * Math.exp(0.45 * z);
  }

  private etapa(nome: string, extraLlm = false): number {
    const d = this.lognormal(MEDIANA[nome] ?? 0.1);
    observar((this.estado.agentes[nome] ??= vazio()), d);
    if (extraLlm) observar((this.estado.llm[nome] ??= vazio()), d * 0.92);
    return d;
  }

  private requisicao(): void {
    const e = this.estado;
    const r = this.rnd();
    let acc = 0;
    let rota: string = ESPECIALISTAS[0];
    for (let i = 0; i < ESPECIALISTAS.length; i++) {
      acc += PESOS_ROTA[i];
      if (r <= acc) {
        rota = ESPECIALISTAS[i];
        break;
      }
    }
    e.rotas[rota] = (e.rotas[rota] ?? 0) + 1;
    e.http.total += 1;

    if (this.rnd() < 0.04) {
      e.http.erros5xx += 1;
      return;
    }
    if (this.rnd() < 0.03) {
      e.guardrail.entrada_insegura = (e.guardrail.entrada_insegura ?? 0) + 1;
      e.chats.naoResolvidas += 1;
      observar(e.chat, this.etapa("guardrail_entrada"));
      return;
    }
    if (this.rnd() < 0.012) e.cota += 1;

    let total = this.etapa("guardrail_entrada") + this.etapa("memoria") + this.etapa("verificar_compactacao");
    if (this.rnd() < 0.08) total += this.etapa("resumir_memoria", true);
    total += this.etapa("orquestrador", true);
    total += this.etapa(rota, true);
    total += this.etapa("juiz_saida", true);
    const reprovou = this.rnd() < 0.1;
    if (reprovou) {
      e.juiz.reprovadas += 1;
      total += this.etapa("correcao", true) + this.etapa("juiz_saida", true);
    } else {
      e.juiz.aprovadas += 1;
    }
    total += this.etapa("guardrail_saida");
    observar(e.chat, total);

    const resolvida = this.rnd() > (reprovou ? 0.4 : 0.03);
    if (resolvida) e.chats.resolvidas += 1;
    else e.chats.naoResolvidas += 1;

    const entrada = Math.round(1800 + this.rnd() * 1800);
    const saida = Math.round(260 + this.rnd() * 320);
    e.tokens.entrada += entrada;
    e.tokens.saida += saida;
    // custo de demonstração: preço esperado do deepseek-flash, cache 50%, fora do pico
    e.custoBrl += custoRespostaUsd(entrada, saida, 0.5, 0.2) * COTACAO_RESERVA.usdBrl;
  }

  /** Avança a simulação até o instante `t`, gerando um número variável de requisições por segundo. */
  avancar(t: number, taxaPorSegundo = 0.9): Snapshot {
    const dt = Math.max(0, (t - this.estado.t) / 1000);
    let n = 0;
    const esperado = dt * taxaPorSegundo * (0.6 + this.rnd() * 0.9);
    n = Math.floor(esperado) + (this.rnd() < esperado - Math.floor(esperado) ? 1 : 0);
    for (let i = 0; i < n; i++) this.requisicao();
    this.estado.t = t;
    return this.foto();
  }

  /** Foto independente do estado interno (histogramas copiados). */
  foto(): Snapshot {
    const e = this.estado;
    const copiaMapa = (m: Record<string, Hist>): Record<string, Hist> =>
      Object.fromEntries(Object.entries(m).map(([k, h]) => [k, copiar(h)]));
    return {
      ...e,
      http: { ...e.http },
      chats: { ...e.chats },
      tokens: { ...e.tokens },
      chat: copiar(e.chat),
      agentes: copiaMapa(e.agentes),
      llm: copiaMapa(e.llm),
      rotas: { ...e.rotas },
      juiz: { ...e.juiz },
      guardrail: { ...e.guardrail },
    };
  }

  /** Histórico pré-aquecido: `minutos` de tráfego simulado terminando em `agora`, uma foto a cada `passoMs`. */
  preaquecer(agora: number, minutos = 5, passoMs = 2000): Snapshot[] {
    const fotos: Snapshot[] = [];
    this.estado.t = agora - minutos * 60_000;
    this.estado.inicioProcesso = this.estado.t;
    for (let t = this.estado.t + passoMs; t <= agora; t += passoMs) fotos.push(this.avancar(t));
    return fotos;
  }

  get inicioSimulacao(): number {
    return this.inicio;
  }
}

export function custosDemo(s: Snapshot): CustosApi {
  const n = s.chats.resolvidas + s.chats.naoResolvidas;
  const rate = n ? s.chats.resolvidas / n : 0;
  const cen = [100, 1000].map((usuarios) => {
    const r = simular({
      usuarios,
      solicitacoesPorUsuarioSemana: 5,
      tokensEntradaPorResposta: n ? s.tokens.entrada / n : 0,
      tokensSaidaPorResposta: n ? s.tokens.saida / n : 0,
      cacheHit: 0.5,
      fatiaPico: 0.2,
      resolucao: rate,
      usdBrl: COTACAO_RESERVA.usdBrl,
      infraSemanalBrl: 0,
      beneficioPorResolucaoBrl: 0,
    });
    return {
      usuarios_semanais: usuarios,
      custo_total_brl: r.custoTotalSemanaBrl,
      custo_por_resolucao_brl: r.custoPorResolucaoBrl,
      roi: null,
    };
  });
  return {
    amostras: {
      solicitacoes: n,
      resolvidas: s.chats.resolvidas,
      taxa_resolucao: n ? rate : null,
      custo_total_brl: s.custoBrl,
      tokens_entrada: s.tokens.entrada,
      tokens_saida: s.tokens.saida,
    },
    premissas: {
      solicitacoes_por_usuario_semana: 5,
      preco_entrada_por_1m_brl: 0,
      preco_saida_por_1m_brl: 0,
      beneficio_por_resolucao_brl: 0,
      infra_semanal_100_brl: 0,
      infra_semanal_1000_brl: 0,
    },
    cenarios: cen,
  };
}

export const SAUDE_DEMO: Saude = {
  status: "ok",
  servicos: { mongodb: "demo", redis: "demo", qdrant: "demo", neo4j: "demo" },
};
