import { describe, expect, it } from "vitest";
import { custoRespostaUsd, faixaPorSolicitacaoBrl, simular, type EntradaSimulacao } from "./simular";

const base: EntradaSimulacao = {
  usuarios: 100,
  solicitacoesPorUsuarioSemana: 5,
  tokensEntradaPorResposta: 1_000_000,
  tokensSaidaPorResposta: 1_000_000,
  cacheHit: 0,
  fatiaPico: 1,
  resolucao: 0.8,
  usdBrl: 5,
  infraSemanalBrl: 10,
  beneficioPorResolucaoBrl: 2,
};

describe("custoRespostaUsd", () => {
  it("usa cache miss + saída no pico", () => {
    // 1M entrada miss (0,30) + 1M saída (1,20)
    expect(custoRespostaUsd(1_000_000, 1_000_000, 0, 1)).toBeCloseTo(1.5);
  });

  it("usa cache hit fora do pico", () => {
    // 1M entrada hit (0,003) + 1M saída (0,60)
    expect(custoRespostaUsd(1_000_000, 1_000_000, 1, 0)).toBeCloseTo(0.603);
  });

  it("mistura pico e fora do pico proporcionalmente", () => {
    const metade = custoRespostaUsd(1_000_000, 0, 0, 0.5);
    expect(metade).toBeCloseTo((0.3 + 0.15) / 2);
  });
});

describe("simular", () => {
  it("segue as fórmulas de SRE_E_CUSTOS.md", () => {
    const r = simular(base);
    expect(r.solicitacoesSemana).toBe(500);
    expect(r.custoPorSolicitacaoBrl).toBeCloseTo(1.5 * 5);
    expect(r.custoApiSemanaBrl).toBeCloseTo(500 * 7.5);
    expect(r.custoTotalSemanaBrl).toBeCloseTo(500 * 7.5 + 10);
    expect(r.resolucoesSemana).toBe(400);
    expect(r.custoPorResolucaoBrl).toBeCloseTo((500 * 7.5 + 10) / 400);
    expect(r.beneficioSemanaBrl).toBe(800);
    expect(r.roi).toBeCloseTo((800 - 3760) / 3760);
  });

  it("devolve null em vez de dividir por zero", () => {
    expect(simular({ ...base, resolucao: 0 }).custoPorResolucaoBrl).toBeNull();
    const semCusto = simular({ ...base, tokensEntradaPorResposta: 0, tokensSaidaPorResposta: 0, infraSemanalBrl: 0 });
    expect(semCusto.roi).toBeNull();
  });
});

describe("faixaPorSolicitacaoBrl", () => {
  it("o pior caso custa mais que o melhor", () => {
    const f = faixaPorSolicitacaoBrl(2000, 400, 5.5);
    expect(f.pior).toBeGreaterThan(f.melhor);
    expect(f.melhor).toBeGreaterThan(0);
  });
});
