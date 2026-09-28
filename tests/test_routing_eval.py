from __future__ import annotations

import asyncio
from collections import Counter

import pytest

from evals.routing.run import CaseResult, compute_metrics, load_dataset, run_cases
from src.agents.output_parsing import VALID_ROUTES


def _r(esperado: str, obtido: str, dificuldade: str = "facil") -> CaseResult:
    return CaseResult(
        id="x",
        mensagem="m",
        perfil="USUARIO_COMUM",
        dificuldade=dificuldade,
        esperado=esperado,
        obtido=obtido,
        acertou=esperado == obtido,
        latencia_ms=10.0,
        tokens=5,
        saida_bruta="",
    )


def test_dataset_is_valid_and_balanced() -> None:
    cases = load_dataset()
    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids))
    per_route = Counter(case["esperado"] for case in cases)
    assert set(per_route) == set(VALID_ROUTES)
    assert min(per_route.values()) >= 8


def test_compute_metrics_precision_recall_and_confusion() -> None:
    results = [
        _r("faq", "faq"),
        _r("faq", "faq"),
        _r("analytics", "faq"),  # FN de analytics, FP de faq
        _r("analytics", "analytics", "dificil"),
    ]
    metrics = compute_metrics(results)
    assert metrics["acuracia"] == 0.75
    assert metrics["por_rota"]["faq"]["precisao"] == round(2 / 3, 4)
    assert metrics["por_rota"]["faq"]["recall"] == 1.0
    assert metrics["por_rota"]["analytics"]["recall"] == 0.5
    assert metrics["por_rota"]["analytics"]["precisao"] == 1.0
    assert metrics["matriz_confusao"]["analytics"] == {"faq": 1, "analytics": 1}
    assert metrics["por_dificuldade"]["dificil"]["acuracia"] == 1.0
    assert metrics["tokens_totais"] == 20


def test_harness_runs_end_to_end_in_mock(monkeypatch) -> None:
    pytest.importorskip("langgraph")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("TRACING_PROVIDER", "none")
    cases = load_dataset()[:5]
    results = asyncio.run(run_cases(cases, concurrency=2))
    assert [r.id for r in results] == [c["id"] for c in cases]
    assert all(r.obtido in VALID_ROUTES for r in results)


def test_api_errors_are_retried_and_reported_apart_from_misroutes() -> None:
    pytest.importorskip("langgraph")
    from src.agents.factory import AgentSuite

    class FlakyAgents:
        tracing = None
        parse_route = staticmethod(AgentSuite.parse_route)

        def __init__(self):
            self.calls: Counter[str] = Counter()

        async def invoke(self, agent_name, prompt):
            key = "coleta" if "coleta" in prompt.lower() else ("quiz" if "quiz" in prompt.lower() else "outro")
            self.calls[key] += 1
            if key == "coleta" and self.calls[key] == 1:
                raise RuntimeError("429 Resource exhausted")  # 1ª falha, 2ª passa
            if key == "quiz":
                raise RuntimeError("503 unavailable")  # sempre falha
            if key == "outro":
                return "desculpe, não entendi"  # sem rota
            return '{"route": "coletas"}'

    cases = [
        {"id": "a", "mensagem": "Quando é a coleta?", "perfil": "SINDICO_RESIDENCIAL", "esperado": "coletas"},
        {"id": "b", "mensagem": "Meu quiz de ontem", "perfil": "MORADOR_RESIDENCIAL", "esperado": "analytics"},
        {"id": "c", "mensagem": "Oi", "perfil": "USUARIO_COMUM", "esperado": "faq"},
    ]
    agents = FlakyAgents()
    results = asyncio.run(run_cases(cases, concurrency=1, retries=2, backoff_seconds=0.01, agents=agents))
    by_id = {r.id: r for r in results}
    assert by_id["a"].status == "ok" and by_id["a"].acertou and by_id["a"].tentativas == 2
    assert by_id["b"].status == "erro_api" and by_id["b"].obtido == "(erro_api)" and "503" in by_id["b"].erro
    assert by_id["c"].status == "saida_invalida" and by_id["c"].obtido == "faq"
    metrics = compute_metrics(results)
    assert metrics["erros_api"] == 1 and metrics["saidas_invalidas"] == 1
    assert metrics["acuracia"] == round(2 / 3, 4)
    assert metrics["acuracia_respondidos"] == 1.0


def test_rate_limiter_spaces_calls() -> None:
    from evals.routing.run import _RateLimiter

    async def go():
        limiter = _RateLimiter(rpm=600)  # 1 a cada 0,1 s
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        for _ in range(4):
            await limiter.wait()
        return loop.time() - t0

    assert asyncio.run(go()) >= 0.29
