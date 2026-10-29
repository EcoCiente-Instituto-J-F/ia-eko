import type { Hist, Snapshot } from "../types";

/** Diferença entre dois histogramas cumulativos (b - a): o que aconteceu entre as duas fotos. */
export function diferenca(b: Hist, a: Hist | undefined): Hist {
  if (!a) return b;
  const anterior = new Map(a.buckets);
  return {
    buckets: b.buckets.map(([le, n]) => [le, Math.max(0, n - (anterior.get(le) ?? 0))] as [number, number]),
    sum: Math.max(0, b.sum - a.sum),
    count: Math.max(0, b.count - a.count),
  };
}

/**
 * Quantil por interpolação linear dentro do bucket (mesma ideia do histogram_quantile do PromQL).
 * Devolve null sem amostras. Acima do último bucket finito, devolve o limite dele.
 */
export function quantil(h: Hist | undefined, q: number): number | null {
  if (!h || h.count <= 0 || h.buckets.length === 0) return null;
  const alvo = q * h.count;
  let limiteAnterior = 0;
  let contagemAnterior = 0;
  for (const [le, n] of h.buckets) {
    if (n >= alvo) {
      if (!Number.isFinite(le)) return limiteAnterior;
      const faixa = n - contagemAnterior;
      if (faixa <= 0) return le;
      return limiteAnterior + (le - limiteAnterior) * ((alvo - contagemAnterior) / faixa);
    }
    limiteAnterior = Number.isFinite(le) ? le : limiteAnterior;
    contagemAnterior = n;
  }
  return limiteAnterior;
}

export function media(h: Hist | undefined): number | null {
  return h && h.count > 0 ? h.sum / h.count : null;
}

/** Foto mais antiga ainda dentro da janela (em ms); null quando a janela é "desde o início". */
export function baseDaJanela(historico: Snapshot[], atual: Snapshot, janelaMs: number | null): Snapshot | null {
  if (janelaMs === null) return null;
  const corte = atual.t - janelaMs;
  for (const s of historico) if (s.t >= corte && s !== atual) return s;
  return null;
}

/** Aplica a janela a um recorte de contadores: atual - base (ou atual, sem base). */
export function delta(atual: number, base: number | undefined): number {
  return base === undefined ? atual : Math.max(0, atual - base);
}
