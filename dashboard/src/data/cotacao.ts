import { COTACAO_RESERVA } from "./preco";

export interface Cotacao {
  usdBrl: number;
  data: string;
  /** "frankfurter" quando veio da API; "reserva" quando usamos o valor salvo */
  fonte: "frankfurter" | "reserva";
}

const URL_API = "https://api.frankfurter.dev/v1/latest?base=USD&symbols=BRL";
const CHAVE = "ecociente.cotacao";
const VALIDADE_MS = 6 * 60 * 60 * 1000;

function lerCache(): (Cotacao & { em: number }) | null {
  try {
    const bruto = localStorage.getItem(CHAVE);
    return bruto ? (JSON.parse(bruto) as Cotacao & { em: number }) : null;
  } catch {
    return null;
  }
}

/** Busca US$→R$ na Frankfurter (gratuita, sem token). Cache de 6 h; cai para a cotação salva se falhar. */
export async function buscarCotacao(): Promise<Cotacao> {
  const cache = lerCache();
  if (cache && Date.now() - cache.em < VALIDADE_MS) return cache;
  try {
    const r = await fetch(URL_API);
    if (!r.ok) throw new Error(String(r.status));
    const j = (await r.json()) as { date: string; rates: { BRL: number } };
    const nova: Cotacao = { usdBrl: j.rates.BRL, data: j.date, fonte: "frankfurter" };
    try {
      localStorage.setItem(CHAVE, JSON.stringify({ ...nova, em: Date.now() }));
    } catch {
      /* sem armazenamento: segue sem cache */
    }
    return nova;
  } catch {
    return cache ?? { ...COTACAO_RESERVA, fonte: "reserva" };
  }
}
