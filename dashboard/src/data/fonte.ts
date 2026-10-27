import type { CustosApi, Estado, Origem, Saude, Snapshot } from "../types";
import { custosDemo, GeradorDemo, SAUDE_DEMO } from "./demo";
import { normalizar } from "./prom";

const INTERVALO_MS = 2000;
const RETENCAO_MS = 10 * 60_000;
const TIMEOUT_MS = 4000;
const FALHAS_PARA_DEMO = 2;
const REPROVAR_AO_VIVO_MS = 10_000;

type Ouvinte = (e: Estado) => void;

async function buscar(url: string): Promise<Response> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
  try {
    const r = await fetch(url, { signal: ctl.signal, cache: "no-store" });
    if (!r.ok) throw new Error(`${url} respondeu ${r.status}`);
    return r;
  } finally {
    clearTimeout(timer);
  }
}

/** Lê o /metrics ao vivo (ou gera demonstração) e entrega um Estado novo a cada ciclo. */
export class Fonte {
  private ouvintes = new Set<Ouvinte>();
  private historico: Snapshot[] = [];
  private custos: CustosApi | null = null;
  private saude: Saude | null = null;
  private origem: Origem = "ao-vivo";
  private motivoDemo: string | null = null;
  private forcadoDemo = false;
  private falhas = 0;
  private ciclo = 0;
  private timer: number | undefined;
  private demo: GeradorDemo | null = null;
  private ultimaTentativaAoVivo = 0;
  private pausado = false;
  readonly observandoDesde = Date.now();

  assinar(fn: Ouvinte): () => void {
    this.ouvintes.add(fn);
    return () => this.ouvintes.delete(fn);
  }

  iniciar(): void {
    void this.rodar();
  }

  pausar(valor: boolean): void {
    this.pausado = valor;
    if (!valor) void this.rodar();
  }

  get estaPausado(): boolean {
    return this.pausado;
  }

  /** Alterna entre ao vivo e demonstração por escolha do usuário. */
  definirModo(modo: Origem): void {
    this.forcadoDemo = modo === "demo";
    this.historico = [];
    this.demo = null;
    this.falhas = 0;
    this.motivoDemo = null;
    this.origem = modo;
    void this.rodar();
  }

  private agendar(): void {
    window.clearTimeout(this.timer);
    if (!this.pausado) this.timer = window.setTimeout(() => void this.rodar(), INTERVALO_MS);
  }

  private async rodar(): Promise<void> {
    window.clearTimeout(this.timer);
    if (this.pausado) return;
    this.ciclo += 1;
    try {
      if (this.forcadoDemo || this.origem === "demo") await this.cicloDemo();
      else await this.cicloAoVivo();
    } finally {
      this.emitir();
      this.agendar();
    }
  }

  private async cicloAoVivo(): Promise<void> {
    try {
      const texto = await (await buscar("/metrics")).text();
      const agora = Date.now();
      this.registrar(normalizar(texto, agora));
      this.falhas = 0;
      if (this.ciclo % 3 === 1) await this.atualizarSecundarios();
    } catch (e) {
      this.falhas += 1;
      if (this.falhas >= FALHAS_PARA_DEMO) {
        this.motivoDemo = e instanceof Error ? e.message : "API indisponível";
        this.entrarEmDemo();
      }
    }
  }

  private async atualizarSecundarios(): Promise<void> {
    const [c, h] = await Promise.allSettled([buscar("/api/v1/costs"), buscar("/health")]);
    if (c.status === "fulfilled") this.custos = (await c.value.json()) as CustosApi;
    if (h.status === "fulfilled") {
      const j = (await h.value.json()) as { status: "ok" | "degraded"; services: Record<string, string> };
      this.saude = { status: j.status, servicos: j.services };
    } else {
      this.saude = { status: "indisponivel", servicos: {} };
    }
  }

  private entrarEmDemo(): void {
    this.origem = "demo";
    this.historico = [];
    this.demo = null;
    this.ultimaTentativaAoVivo = Date.now();
  }

  private async cicloDemo(): Promise<void> {
    const agora = Date.now();
    if (!this.demo) {
      this.demo = new GeradorDemo(agora);
      this.historico = this.demo.preaquecer(agora);
    }
    this.registrar(this.demo.avancar(agora));
    const atual = this.historico[this.historico.length - 1];
    this.custos = custosDemo(atual);
    this.saude = SAUDE_DEMO;
    // Se caiu sozinho, volta ao vivo assim que a API responder de novo.
    if (!this.forcadoDemo && agora - this.ultimaTentativaAoVivo > REPROVAR_AO_VIVO_MS) {
      this.ultimaTentativaAoVivo = agora;
      try {
        await buscar("/metrics");
        this.origem = "ao-vivo";
        this.motivoDemo = null;
        this.historico = [];
        this.demo = null;
        this.falhas = 0;
      } catch {
        /* continua em demonstração */
      }
    }
  }

  private registrar(s: Snapshot): void {
    this.historico.push(s);
    const corte = s.t - RETENCAO_MS;
    while (this.historico.length > 2 && this.historico[0].t < corte) this.historico.shift();
  }

  private emitir(): void {
    const estado: Estado = {
      origem: this.origem,
      motivoDemo: this.forcadoDemo ? null : this.motivoDemo,
      atual: this.historico[this.historico.length - 1] ?? null,
      historico: this.historico,
      custos: this.custos,
      saude: this.saude,
      observandoDesde: this.observandoDesde,
    };
    for (const fn of this.ouvintes) fn(estado);
  }
}
