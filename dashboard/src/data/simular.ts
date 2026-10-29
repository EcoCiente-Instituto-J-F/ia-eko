import { USD_POR_1M } from "./preco";

export interface EntradaSimulacao {
  usuarios: number;
  solicitacoesPorUsuarioSemana: number;
  tokensEntradaPorResposta: number;
  tokensSaidaPorResposta: number;
  /** 0–1: parte dos tokens de entrada servida do cache da DeepSeek */
  cacheHit: number;
  /** 0–1: parte do tráfego que cai no horário de pico */
  fatiaPico: number;
  /** 0–1: taxa de resolução (resposta aprovada pelo juiz) */
  resolucao: number;
  usdBrl: number;
  infraSemanalBrl: number;
  beneficioPorResolucaoBrl: number;
}

export interface Resultado {
  solicitacoesSemana: number;
  custoPorSolicitacaoBrl: number;
  custoApiSemanaBrl: number;
  custoTotalSemanaBrl: number;
  resolucoesSemana: number;
  /** null quando não há resolução (divisão por zero) */
  custoPorResolucaoBrl: number | null;
  beneficioSemanaBrl: number;
  /** null quando o custo total é zero */
  roi: number | null;
}

/** Custo em US$ de uma resposta, dado o mix de preço (pico/fora do pico) e de cache. */
export function custoRespostaUsd(
  tokensEntrada: number,
  tokensSaida: number,
  cacheHit: number,
  fatiaPico: number,
): number {
  const preco = (campo: "entradaCacheHit" | "entradaCacheMiss" | "saida"): number =>
    fatiaPico * USD_POR_1M.pico[campo] + (1 - fatiaPico) * USD_POR_1M.foraDoPico[campo];
  const entrada = tokensEntrada * (cacheHit * preco("entradaCacheHit") + (1 - cacheHit) * preco("entradaCacheMiss"));
  return (entrada + tokensSaida * preco("saida")) / 1_000_000;
}

const dividir = (a: number, b: number): number | null => (b > 0 ? a / b : null);

/** Mesmas fórmulas de docs/SRE_E_CUSTOS.md, com o preço da DeepSeek separado por cache e horário. */
export function simular(e: EntradaSimulacao): Resultado {
  const solicitacoes = e.usuarios * e.solicitacoesPorUsuarioSemana;
  const porSolicitacao = custoRespostaUsd(e.tokensEntradaPorResposta, e.tokensSaidaPorResposta, e.cacheHit, e.fatiaPico) * e.usdBrl;
  const api = solicitacoes * porSolicitacao;
  const total = api + e.infraSemanalBrl;
  const resolucoes = solicitacoes * e.resolucao;
  const beneficio = resolucoes * e.beneficioPorResolucaoBrl;
  return {
    solicitacoesSemana: solicitacoes,
    custoPorSolicitacaoBrl: porSolicitacao,
    custoApiSemanaBrl: api,
    custoTotalSemanaBrl: total,
    resolucoesSemana: resolucoes,
    custoPorResolucaoBrl: dividir(total, resolucoes),
    beneficioSemanaBrl: beneficio,
    roi: total > 0 ? (beneficio - total) / total : null,
  };
}

/** Faixa de custo de API por solicitação: pior caso (tudo no pico, sem cache) e melhor (fora do pico, cache alto). */
export function faixaPorSolicitacaoBrl(
  tokensEntrada: number,
  tokensSaida: number,
  usdBrl: number,
  cacheOtimista = 0.9,
): { pior: number; melhor: number } {
  return {
    pior: custoRespostaUsd(tokensEntrada, tokensSaida, 0, 1) * usdBrl,
    melhor: custoRespostaUsd(tokensEntrada, tokensSaida, cacheOtimista, 0) * usdBrl,
  };
}
