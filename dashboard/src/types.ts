/** Histograma Prometheus cumulativo: buckets [limite superior, contagem acumulada]. */
export interface Hist {
  buckets: Array<[number, number]>;
  sum: number;
  count: number;
}

/** Foto normalizada de tudo que o painel lê de /metrics (valores acumulados desde o início do processo). */
export interface Snapshot {
  /** instante da leitura, em ms */
  t: number;
  /** início do processo da API em ms, quando o Prometheus o expõe */
  inicioProcesso: number | null;
  http: { total: number; erros5xx: number };
  chats: { resolvidas: number; naoResolvidas: number };
  custoBrl: number;
  tokens: { entrada: number; saida: number };
  /** latência do /chat de ponta a ponta */
  chat: Hist;
  /** latência por nó do grafo (guardrail_entrada, memoria, orquestrador, especialistas, juiz_saida...) */
  agentes: Record<string, Hist>;
  /** latência das chamadas ao LLM por agente */
  llm: Record<string, Hist>;
  rotas: Record<string, number>;
  juiz: { aprovadas: number; reprovadas: number };
  guardrail: Record<string, number>;
  cota: number;
}

export type Origem = "ao-vivo" | "demo";

export interface Premissas {
  solicitacoesPorUsuarioSemana: number;
  beneficioPorResolucaoBrl: number;
  infraSemanal100Brl: number;
  infraSemanal1000Brl: number;
}

/** Resposta de GET /api/v1/costs (campos usados pelo painel). */
export interface CustosApi {
  amostras: {
    solicitacoes: number;
    resolvidas: number;
    taxa_resolucao: number | null;
    custo_total_brl: number;
    tokens_entrada: number;
    tokens_saida: number;
  };
  premissas: {
    solicitacoes_por_usuario_semana: number;
    preco_entrada_por_1m_brl: number;
    preco_saida_por_1m_brl: number;
    beneficio_por_resolucao_brl: number;
    infra_semanal_100_brl: number;
    infra_semanal_1000_brl: number;
  };
  cenarios: Array<{
    usuarios_semanais: number;
    custo_total_brl: number | null;
    custo_por_resolucao_brl: number | null;
    roi: number | null;
  }>;
}

export interface Saude {
  status: "ok" | "degraded" | "indisponivel";
  servicos: Record<string, string>;
}

/** Estado completo que a interface desenha a cada ciclo. */
export interface Estado {
  origem: Origem;
  /** motivo do modo demo quando a API caiu sozinha */
  motivoDemo: string | null;
  atual: Snapshot | null;
  /** fotos recentes, da mais antiga para a mais nova */
  historico: Snapshot[];
  custos: CustosApi | null;
  saude: Saude | null;
  observandoDesde: number;
}
