"""Avaliação offline do roteamento do orquestrador.

Roda cada caso de `dataset.jsonl` pelo MESMO prompt e parser da produção
(`EcoGraphRuntime.build_orchestrator_prompt` + `AgentSuite.parse_route`) e
mede acurácia, precisão/recall/F1 por rota e a matriz de confusão.

Uso:
    # modelo configurado no .env (LLM_PROVIDER=ollama|groq|gemini)
    python -m evals.routing.run
    python -m evals.routing.run --min-accuracy 0.85 --concurrency 4
    # heurística determinística (sem LLM) — só valida o harness
    LLM_PROVIDER=mock python -m evals.routing.run

Saída: resumo no terminal + `evals/reports/routing-<data>.json` e `.md`.
Código de saída 1 se a acurácia ficar abaixo de --min-accuracy (útil no CI).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from src.agents.output_parsing import VALID_ROUTES

DATASET = Path(__file__).with_name("dataset.jsonl")
REPORTS = Path(__file__).resolve().parents[1] / "reports"


@dataclass(slots=True)
class CaseResult:
    id: str
    mensagem: str
    perfil: str
    dificuldade: str
    esperado: str
    obtido: str
    acertou: bool
    latencia_ms: float
    tokens: int
    saida_bruta: str


def load_dataset(path: Path = DATASET) -> list[dict[str, Any]]:
    cases = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        case = json.loads(line)
        if case["esperado"] not in VALID_ROUTES:
            raise ValueError(f"linha {line_no}: rota esperada inválida {case['esperado']!r}")
        cases.append(case)
    return cases


def compute_metrics(results: Iterable[CaseResult]) -> dict[str, Any]:
    results = list(results)
    total = len(results)
    hits = sum(r.acertou for r in results)
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    for r in results:
        confusion[r.esperado][r.obtido] += 1

    per_route = {}
    for route in VALID_ROUTES:
        tp = confusion[route][route]
        fp = sum(confusion[other][route] for other in VALID_ROUTES if other != route)
        fn = sum(count for obtido, count in confusion[route].items() if obtido != route)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_route[route] = {
            "precisao": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "suporte": tp + fn,
        }

    by_difficulty: dict[str, dict[str, float]] = {}
    for level in sorted({r.dificuldade for r in results}):
        subset = [r for r in results if r.dificuldade == level]
        by_difficulty[level] = {
            "acuracia": round(sum(r.acertou for r in subset) / len(subset), 4),
            "casos": len(subset),
        }

    latencies = sorted(r.latencia_ms for r in results)
    return {
        "casos": total,
        "acertos": hits,
        "acuracia": round(hits / total, 4) if total else 0.0,
        "macro_f1": round(sum(m["f1"] for m in per_route.values()) / len(per_route), 4),
        "por_rota": per_route,
        "por_dificuldade": by_difficulty,
        "matriz_confusao": {esperado: dict(obtidos) for esperado, obtidos in confusion.items()},
        "latencia_p50_ms": latencies[len(latencies) // 2] if latencies else 0.0,
        "latencia_p95_ms": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else 0.0,
        "tokens_totais": sum(r.tokens for r in results),
    }


async def run_cases(cases: list[dict[str, Any]], *, concurrency: int = 2) -> list[CaseResult]:
    # Imports tardios: `compute_metrics` pode ser testado sem LangGraph instalado.
    from src.agents.factory import AgentSuite
    from src.agents.graph import EcoGraphRuntime
    from src.core.config import Settings
    from src.observability.tracing import TracingService, conversation_trace
    from src.shared.context import UserContext

    settings = Settings.from_env()
    agents = AgentSuite(settings, TracingService(settings))
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def one(case: dict[str, Any]) -> CaseResult:
        user = UserContext(user_id=0, perfil=case["perfil"], condominio_id=None)
        prompt = EcoGraphRuntime.build_orchestrator_prompt(case["mensagem"], user, {})
        async with semaphore:
            with conversation_trace(
                session_id=f"eval-routing-{case['id']}",
                user_id=0,
                request_id=case["id"],
                perfil=case["perfil"],
            ) as trace:
                started = time.perf_counter()
                raw = await agents.invoke("orquestrador", prompt)
                latency = (time.perf_counter() - started) * 1000
        obtido = agents.parse_route(raw)
        return CaseResult(
            id=case["id"],
            mensagem=case["mensagem"],
            perfil=case["perfil"],
            dificuldade=case.get("dificuldade", "media"),
            esperado=case["esperado"],
            obtido=obtido,
            acertou=obtido == case["esperado"],
            latencia_ms=round(latency, 1),
            tokens=trace.usage.total_tokens,
            saida_bruta=raw[:500],
        )

    try:
        return list(await asyncio.gather(*(one(case) for case in cases)))
    finally:
        agents.tracing.shutdown()


def render_markdown(metrics: dict[str, Any], results: list[CaseResult], provider: str, model: str) -> str:
    lines = [
        f"# Avaliação de roteamento — {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"- Provedor/modelo: `{provider}` / `{model}`",
        f"- Acurácia: **{metrics['acuracia']:.1%}** ({metrics['acertos']}/{metrics['casos']}) · macro-F1 {metrics['macro_f1']:.3f}",
        f"- Latência p50/p95: {metrics['latencia_p50_ms']:.0f} / {metrics['latencia_p95_ms']:.0f} ms · tokens: {metrics['tokens_totais']}",
        "",
        "| Rota | Precisão | Recall | F1 | Casos |",
        "| --- | --- | --- | --- | --- |",
    ]
    for route, m in metrics["por_rota"].items():
        lines.append(f"| {route} | {m['precisao']:.2f} | {m['recall']:.2f} | {m['f1']:.2f} | {m['suporte']} |")
    lines += ["", "| Dificuldade | Acurácia | Casos |", "| --- | --- | --- |"]
    for level, m in metrics["por_dificuldade"].items():
        lines.append(f"| {level} | {m['acuracia']:.1%} | {m['casos']} |")
    lines += ["", "## Matriz de confusão (linhas = esperado, colunas = obtido)", ""]
    header = "| esperado \\ obtido | " + " | ".join(VALID_ROUTES) + " |"
    lines += [header, "| --- |" + " --- |" * len(VALID_ROUTES)]
    for esperado in VALID_ROUTES:
        row = metrics["matriz_confusao"].get(esperado, {})
        lines.append(f"| {esperado} | " + " | ".join(str(row.get(o, 0)) for o in VALID_ROUTES) + " |")
    errors = [r for r in results if not r.acertou]
    lines += ["", f"## Erros ({len(errors)})", ""]
    for r in errors:
        lines.append(f"- `{r.id}` [{r.dificuldade}] esperado **{r.esperado}**, obtido **{r.obtido}** — {r.mensagem}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--limit", type=int, default=None, help="avaliar só os N primeiros casos")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--min-accuracy", type=float, default=0.0, help="falha (exit 1) abaixo deste valor")
    parser.add_argument("--no-report", action="store_true", help="não gravar arquivos em evals/reports")
    args = parser.parse_args(argv)

    from src.core.config import Settings

    settings = Settings.from_env()
    model = {
        "ollama": settings.ollama_model,
        "groq": settings.groq_model,
        "gemini": settings.gemini_model,
    }.get(settings.llm_provider, "heuristica-mock")

    cases = load_dataset(args.dataset)[: args.limit]
    results = asyncio.run(run_cases(cases, concurrency=args.concurrency))
    metrics = compute_metrics(results)
    markdown = render_markdown(metrics, results, settings.llm_provider, model)
    print(markdown)

    if not args.no_report:
        REPORTS.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = REPORTS / f"routing-{settings.llm_provider}-{stamp}"
        base.with_suffix(".md").write_text(markdown, encoding="utf-8")
        base.with_suffix(".json").write_text(
            json.dumps(
                {"provider": settings.llm_provider, "model": model, "metricas": metrics, "resultados": [asdict(r) for r in results]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Relatório: {base.with_suffix('.md')}")

    if metrics["acuracia"] < args.min_accuracy:
        print(f"FALHOU: acurácia {metrics['acuracia']:.1%} < mínimo {args.min_accuracy:.1%}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
