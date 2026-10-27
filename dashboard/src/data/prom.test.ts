import { describe, expect, it } from "vitest";
import { normalizar } from "./prom";
import { baseDaJanela, diferenca, quantil } from "./quantil";

const TEXTO = `
# HELP ecociente_http_requests_total Total
ecociente_http_requests_total{method="POST",route="/chat",status="200"} 8.0
ecociente_http_requests_total{method="POST",route="/chat",status="500"} 2.0
ecociente_http_request_errors_total{method="POST",route="/chat"} 2.0
ecociente_http_request_duration_seconds_bucket{method="POST",route="/chat",le="1.0"} 4.0
ecociente_http_request_duration_seconds_bucket{method="POST",route="/chat",le="2.0"} 9.0
ecociente_http_request_duration_seconds_bucket{method="POST",route="/chat",le="+Inf"} 10.0
ecociente_http_request_duration_seconds_sum{method="POST",route="/chat"} 14.0
ecociente_http_request_duration_seconds_count{method="POST",route="/chat"} 10.0
ecociente_http_request_duration_seconds_count{method="GET",route="/health"} 99.0
ecociente_chats_total{resolved="true"} 7.0
ecociente_chats_total{resolved="false"} 1.0
ecociente_chat_cost_brl_total 0.0421
ecociente_llm_tokens_total{provider="deepseek",agent="faq",kind="input"} 1000.0
ecociente_llm_tokens_total{provider="deepseek",agent="faq",kind="output"} 200.0
ecociente_agent_duration_seconds_bucket{agent="orquestrador",le="0.5"} 3.0
ecociente_agent_duration_seconds_bucket{agent="orquestrador",le="+Inf"} 3.0
ecociente_agent_duration_seconds_count{agent="orquestrador"} 3.0
ecociente_agent_duration_seconds_sum{agent="orquestrador"} 0.9
ecociente_routing_decisions_total{route="faq"} 5.0
ecociente_routing_decisions_total{route="analytics"} 2.0
ecociente_judge_decisions_total{agent="faq",outcome="approved",category="ok"} 6.0
ecociente_judge_decisions_total{agent="faq",outcome="rejected",category="fonte"} 1.0
process_start_time_seconds 1.7e9
`;

describe("normalizar", () => {
  const s = normalizar(TEXTO, 1000);

  it("soma contadores HTTP e erros", () => {
    expect(s.http.total).toBe(10);
    expect(s.http.erros5xx).toBe(2);
  });

  it("separa resolvidas e não resolvidas", () => {
    expect(s.chats).toEqual({ resolvidas: 7, naoResolvidas: 1 });
  });

  it("considera só a rota /chat no histograma de latência total", () => {
    expect(s.chat.count).toBe(10);
    expect(s.chat.sum).toBe(14);
  });

  it("lê tokens, custo, rotas e juiz", () => {
    expect(s.tokens).toEqual({ entrada: 1000, saida: 200 });
    expect(s.custoBrl).toBeCloseTo(0.0421);
    expect(s.rotas).toEqual({ faq: 5, analytics: 2 });
    expect(s.juiz).toEqual({ aprovadas: 6, reprovadas: 1 });
  });

  it("agrupa histogramas por agente", () => {
    expect(s.agentes.orquestrador.count).toBe(3);
  });

  it("converte o início do processo para ms", () => {
    expect(s.inicioProcesso).toBe(1.7e12);
  });
});

describe("quantil", () => {
  const s = normalizar(TEXTO, 1000);

  it("interpola dentro do bucket", () => {
    // p50: alvo 5 está no bucket (1,2]; 4 no anterior, faixa 5 -> 1 + 1 * (1/5)
    expect(quantil(s.chat, 0.5)).toBeCloseTo(1.2);
  });

  it("limita ao último bucket finito acima dele", () => {
    expect(quantil(s.chat, 0.99)).toBe(2);
  });

  it("devolve null sem amostras", () => {
    expect(quantil({ buckets: [], sum: 0, count: 0 }, 0.95)).toBeNull();
    expect(quantil(undefined, 0.95)).toBeNull();
  });
});

describe("janela", () => {
  it("diferenca isola o que aconteceu entre duas fotos", () => {
    const a = { buckets: [[1, 2], [Infinity, 2]] as Array<[number, number]>, sum: 1, count: 2 };
    const b = { buckets: [[1, 5], [Infinity, 6]] as Array<[number, number]>, sum: 4, count: 6 };
    const d = diferenca(b, a);
    expect(d.count).toBe(4);
    expect(d.buckets[0][1]).toBe(3);
  });

  it("baseDaJanela escolhe a foto mais antiga dentro da janela", () => {
    const mk = (t: number) => normalizar("", t);
    const hist = [mk(0), mk(100), mk(200), mk(300)];
    expect(baseDaJanela(hist, hist[3], 150)?.t).toBe(200);
    expect(baseDaJanela(hist, hist[3], null)).toBeNull();
  });
});
