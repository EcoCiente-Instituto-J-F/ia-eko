/**
 * Preços do deepseek-flash (DeepSeek-V4.1-Flash-1017), US$ por 1M de tokens.
 * Fonte: https://api-docs.deepseek.com/quick_start/pricing/ (consultada em 26/10/2026).
 * A DeepSeek pode reajustar os preços; atualize aqui e em docs/PAINEL_DE_CONTROLE.md.
 */
export const MODELO = "deepseek-flash";
export const MODELO_MODO = "sem thinking";
export const PRECOS_CONSULTADOS_EM = "2026-10-26";

export const USD_POR_1M = {
  /** horário de pico: 01–04h e 06–10h UTC, de segunda a sexta (exceto feriados na China) */
  pico: { entradaCacheHit: 0.006, entradaCacheMiss: 0.3, saida: 1.2 },
  foraDoPico: { entradaCacheHit: 0.003, entradaCacheMiss: 0.15, saida: 0.6 },
} as const;

/** 7 h de pico por dia útil x 5 dias, de 168 h na semana: fatia de tráfego no pico se o uso for uniforme. */
export const FATIA_PICO_UNIFORME = 35 / 168;

/** Cotação de reserva, usada só quando a Frankfurter não responde. */
export const COTACAO_RESERVA = { usdBrl: 5.5884, data: "2026-10-26" };
