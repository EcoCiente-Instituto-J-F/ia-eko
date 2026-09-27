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
