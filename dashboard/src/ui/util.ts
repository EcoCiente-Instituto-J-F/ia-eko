import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Coins,
  CircleAlert,
  FlaskConical,
  Gauge,
  Pause,
  Play,
  Radio,
  ShieldCheck,
  TrendingUp,
  createElement,
  type IconNode,
} from "lucide";

const ICONES = {
  atividade: Activity,
  alerta: AlertTriangle,
  ok: CheckCircle2,
  relogio: Clock,
  moeda: Coins,
  erro: CircleAlert,
  demo: FlaskConical,
  medidor: Gauge,
  pausa: Pause,
  tocar: Play,
  vivo: Radio,
  escudo: ShieldCheck,
  tendencia: TrendingUp,
} satisfies Record<string, IconNode>;

export type NomeIcone = keyof typeof ICONES;

/** SVG de contorno arredondado (kit lucide, o mesmo do Figma), pronto para inserir em HTML. */
export function icone(nome: NomeIcone): string {
  const svg = createElement(ICONES[nome]);
  svg.setAttribute("class", "icone");
  svg.setAttribute("aria-hidden", "true");
  svg.removeAttribute("width");
  svg.removeAttribute("height");
  return svg.outerHTML;
}

const ESC: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (s: string): string => s.replace(/[&<>"']/g, (c) => ESC[c]);

/** Troca o innerHTML só quando o texto mudou: evita redesenho e perda de foco/rolagem a cada ciclo. */
export function pintar(el: Element | null, html: string): void {
  if (!el) return;
  const ant = (el as HTMLElement & { __html?: string }).__html;
  if (ant === html) return;
  (el as HTMLElement & { __html?: string }).__html = html;
  el.innerHTML = html;
}

// ───────── formatação pt-BR ─────────
const nf = (min: number, max: number): Intl.NumberFormat =>
  new Intl.NumberFormat("pt-BR", { minimumFractionDigits: min, maximumFractionDigits: max });

export const inteiro = (n: number): string => nf(0, 0).format(Math.round(n));
export const decimal = (n: number, d = 1): string => nf(d, d).format(n);

/** Valores monetários pequenos precisam de mais casas: R$ 0,0042 por resposta não pode virar R$ 0,00. */
export function brl(valor: number | null | undefined, casas?: number): string {
  if (valor === null || valor === undefined || !Number.isFinite(valor)) return "—";
  const abs = Math.abs(valor);
  const d = casas ?? (abs === 0 || abs >= 1 ? 2 : abs >= 0.01 ? 3 : 4);
  return `R$ ${nf(d, d).format(valor)}`;
}

export function segundos(s: number | null | undefined): string {
  if (s === null || s === undefined || !Number.isFinite(s)) return "—";
  if (s < 1) return `${nf(0, 0).format(s * 1000)} ms`;
  return `${nf(2, 2).format(s)} s`;
}

export function percentual(razao: number | null | undefined, casas = 1): string {
  if (razao === null || razao === undefined || !Number.isFinite(razao)) return "—";
  return `${nf(casas, casas).format(razao * 100)}%`;
}

export function compacto(n: number): string {
  return new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

export function duracao(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const p = (n: number): string => String(n).padStart(2, "0");
  return h > 0 ? `${p(h)}:${p(m)}:${p(s)}` : `${p(m)}:${p(s)}`;
}

export function hora(t: number): string {
  return new Date(t).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export const clamp = (n: number, min: number, max: number): number => Math.min(max, Math.max(min, n));
