import type { Hist, Snapshot } from "../types";

interface Amostra {
  nome: string;
  rotulos: Record<string, string>;
  valor: number;
}

const LINHA = /^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+(\S+)(?:\s+\d+)?$/;
const ROTULO = /([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"/g;

/** Lê o formato de texto do Prometheus (exposition format). Ignora comentários e linhas inválidas. */
export function lerTexto(texto: string): Amostra[] {
  const saida: Amostra[] = [];
  for (const bruta of texto.split("\n")) {
    const linha = bruta.trim();
    if (!linha || linha.startsWith("#")) continue;
    const m = LINHA.exec(linha);
    if (!m) continue;
    const valor = m[3] === "+Inf" ? Infinity : Number(m[3]);
    if (Number.isNaN(valor)) continue;
    const rotulos: Record<string, string> = {};
    if (m[2]) for (const r of m[2].matchAll(ROTULO)) rotulos[r[1]] = r[2];
    saida.push({ nome: m[1], rotulos, valor });
  }
  return saida;
}

const soma = (xs: Amostra[]): number => xs.reduce((a, x) => a + x.valor, 0);
const doNome = (xs: Amostra[], nome: string): Amostra[] => xs.filter((x) => x.nome === nome);

/** Monta um histograma por valor de `chave` (ou um só, quando `chave` é null). */
function histogramas(xs: Amostra[], base: string, chave: string | null, filtro?: (x: Amostra) => boolean): Record<string, Hist> {
  const saida: Record<string, Hist> = {};
  const pega = (x: Amostra): string => (chave ? (x.rotulos[chave] ?? "desconhecido") : "_");
  const garante = (k: string): Hist => (saida[k] ??= { buckets: [], sum: 0, count: 0 });
  const mapas = new Map<string, Map<number, number>>();

  for (const x of xs) {
    if (filtro && !filtro(x)) continue;
    if (x.nome === `${base}_bucket`) {
      const k = pega(x);
      const le = x.rotulos.le === "+Inf" ? Infinity : Number(x.rotulos.le);
      const mapa = mapas.get(k) ?? new Map<number, number>();
      mapa.set(le, (mapa.get(le) ?? 0) + x.valor);
      mapas.set(k, mapa);
      garante(k);
    } else if (x.nome === `${base}_sum`) {
      garante(pega(x)).sum += x.valor;
    } else if (x.nome === `${base}_count`) {
      garante(pega(x)).count += x.valor;
    }
  }
  for (const [k, mapa] of mapas) {
    saida[k].buckets = [...mapa.entries()].sort((a, b) => a[0] - b[0]);
  }
  return saida;
}

const rotaDoChat = (x: Amostra): boolean => /\/chat(\/stream)?$/.test(x.rotulos.route ?? "");

function contadorPor(xs: Amostra[], nome: string, chave: string): Record<string, number> {
  const saida: Record<string, number> = {};
  for (const x of doNome(xs, nome)) {
    const k = x.rotulos[chave] ?? "desconhecido";
    saida[k] = (saida[k] ?? 0) + x.valor;
  }
  return saida;
}

/** Converte o texto de /metrics na foto normalizada usada pelo painel. */
export function normalizar(texto: string, t: number): Snapshot {
  const xs = lerTexto(texto);
  const inicio = doNome(xs, "process_start_time_seconds")[0]?.valor;
  const chat = histogramas(xs, "ecociente_http_request_duration_seconds", null, rotaDoChat)._ ?? {
    buckets: [],
    sum: 0,
    count: 0,
  };
  const decisoes = doNome(xs, "ecociente_judge_decisions_total");
  const tokens = doNome(xs, "ecociente_llm_tokens_total");

  return {
    t,
    inicioProcesso: inicio ? inicio * 1000 : null,
    http: {
      total: soma(doNome(xs, "ecociente_http_requests_total")),
      erros5xx: soma(doNome(xs, "ecociente_http_request_errors_total")),
    },
    chats: {
      resolvidas: soma(doNome(xs, "ecociente_chats_total").filter((x) => x.rotulos.resolved === "true")),
      naoResolvidas: soma(doNome(xs, "ecociente_chats_total").filter((x) => x.rotulos.resolved === "false")),
    },
    custoBrl: soma(doNome(xs, "ecociente_chat_cost_brl_total")),
    tokens: {
      entrada: soma(tokens.filter((x) => x.rotulos.kind === "input")),
      saida: soma(tokens.filter((x) => x.rotulos.kind === "output")),
    },
    chat,
    agentes: histogramas(xs, "ecociente_agent_duration_seconds", "agent"),
    llm: histogramas(xs, "ecociente_llm_duration_seconds", "agent"),
    rotas: contadorPor(xs, "ecociente_routing_decisions_total", "route"),
    juiz: {
      aprovadas: soma(decisoes.filter((x) => x.rotulos.outcome === "approved")),
      reprovadas: soma(decisoes.filter((x) => x.rotulos.outcome === "rejected")),
    },
    guardrail: contadorPor(xs, "ecociente_guardrail_blocked_total", "reason"),
    cota: soma(doNome(xs, "ecociente_quota_rejected_total")),
  };
}
