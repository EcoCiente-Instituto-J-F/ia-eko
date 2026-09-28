"""Avaliação offline do roteamento do orquestrador.

Roda cada caso de `dataset.jsonl` pelo MESMO prompt e parser da produção
(`EcoGraphRuntime.build_orchestrator_prompt` + `AgentSuite.parse_route`) e
mede acurácia, precisão/recall/F1 por rota e a matriz de confusão.

Uso:
    # modelo configurado no .env (LLM_PROVIDER=ollama|groq|gemini)
    python -m evals.routing.run
    python -m evals.routing.run --min-accuracy 0.85 --concurrency 4
    # Gemini free tier: limite de requisições por minuto
    LLM_PROVIDER=gemini python -m evals.routing.run --rpm 10 --concurrency 1
    # heurística determinística (sem LLM) — só valida o harness
    LLM_PROVIDER=mock python -m evals.routing.run

Falha de API (429, timeout) é tentada de novo com backoff; se persistir, o caso
entra como `erro_api` e NÃO como erro de roteamento. Saída sem rota legível
entra como `saida_invalida` (em produção vira `faq`, o fallback seguro).

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

from src.agents.output_parsing import VALID_ROUTES, parse_route_strict

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
    # ok | saida_invalida (sem rota legível → faq) | erro_api (não respondeu)
    status: str = "ok"
    tentativas: int = 1
    erro: str = ""


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

    respondidos = [r for r in results if r.status != "erro_api"]
    latencies = sorted(r.latencia_ms for r in respondidos)
    return {
        "casos": total,
        "acertos": hits,
        "acuracia": round(hits / total, 4) if total else 0.0,
        # Sem os casos em que a API falhou: mede só o roteamento em si.
        "acuracia_respondidos": round(sum(r.acertou for r in respondidos) / len(respondidos), 4) if respondidos else 0.0,
        "erros_api": total - len(respondidos),
        "saidas_invalidas": sum(r.status == "saida_invalida" for r in results),
        "macro_f1": round(sum(m["f1"] for m in per_route.values()) / len(per_route), 4),
        "por_rota": per_route,
        "por_dificuldade": by_difficulty,
        "matriz_confusao": {esperado: dict(obtidos) for esperado, obtidos in confusion.items()},
        "latencia_p50_ms": latencies[len(latencies) // 2] if latencies else 0.0,
        "latencia_p95_ms": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else 0.0,
        "tokens_totais": sum(r.tokens for r in results),
    }


class _RateLimiter:
    """Espaça o INÍCIO das chamadas para caber em `rpm` requisições/minuto."""

    def __init__(self, rpm: float | None):
        self.interval = 60.0 / rpm if rpm else 0.0
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        if not self.interval:
            return
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            if self._next > now:
                await asyncio.sleep(self._next - now)
                now = self._next
            self._next = now + self.interval


async def run_cases(
    cases: list[dict[str, Any]],
    *,
    concurrency: int = 2,
    rpm: float | None = None,
    retries: int = 3,
    backoff_seconds: float = 5.0,
    agents: Any = None,
) -> list[CaseResult]:
    # Imports tardios: `compute_metrics` pode ser testado sem LangGraph instalado.
    from src.agents.factory import AgentSuite
    from src.agents.graph import EcoGraphRuntime
    from src.core.config import Settings
    from src.observability.tracing import TracingService, conversation_trace
    from src.shared.context import UserContext

    if agents is None:
        settings = Settings.from_env()
        agents = AgentSuite(settings, TracingService(settings))
    semaphore = asyncio.Semaphore(max(1, concurrency))
    limiter = _RateLimiter(rpm)
    retries = max(1, retries)  # 0 significaria não chamar o modelo

    async def one(case: dict[str, Any]) -> CaseResult:
        user = UserContext(user_id=0, perfil=case["perfil"], condominio_id=None)
        prompt = EcoGraphRuntime.build_orchestrator_prompt(case["mensagem"], user, {})
        raw, erro, latency, tokens, attempt = "", "", 0.0, 0, 0
        async with semaphore:
            for attempt in range(1, retries + 1):
                await limiter.wait()
                with conversation_trace(
                    session_id=f"eval-routing-{case['id']}",
                    user_id=0,
                    request_id=case["id"],
                    perfil=case["perfil"],
                ) as trace:
                    started = time.perf_counter()
                    try:
                        raw = await agents.invoke("orquestrador", prompt)
                        erro = ""
                    except Exception as exc:  # noqa: BLE001 - qualquer falha de API
                        erro = f"{type(exc).__name__}: {str(exc)[:200]}"
                    latency = (time.perf_counter() - started) * 1000
                    tokens = trace.usage.total_tokens
                if not erro:
                    break
                if attempt < retries:
                    # 429/cota: espera crescente antes de tentar de novo.
                    await asyncio.sleep(backoff_seconds * 2 ** (attempt - 1))

        dificuldade = case.get("dificuldade", "media")
        if erro:
            return CaseResult(
                id=case["id"], mensagem=case["mensagem"], perfil=case["perfil"], dificuldade=dificuldade,
                esperado=case["esperado"], obtido="(erro_api)", acertou=False, latencia_ms=round(latency, 1),
                tokens=tokens, saida_bruta="", status="erro_api", tentativas=attempt, erro=erro,
            )
        estrita = parse_route_strict(raw)
        obtido = estrita or agents.parse_route(raw)
        return CaseResult(
            id=case["id"],
            mensagem=case["mensagem"],
            perfil=case["perfil"],
            dificuldade=dificuldade,
            esperado=case["esperado"],
            obtido=obtido,
            acertou=obtido == case["esperado"],
            latencia_ms=round(latency, 1),
            tokens=tokens,
            saida_bruta=raw[:500],
            status="ok" if estrita else "saida_invalida",
            tentativas=attempt,
        )

    try:
        return list(await asyncio.gather(*(one(case) for case in cases)))
    finally:
        tracing = getattr(agents, "tracing", None)
        if tracing is not None:
            tracing.shutdown()


def render_markdown(metrics: dict[str, Any], results: list[CaseResult], provider: str, model: str) -> str:
    lines = [
        f"# Avaliação de roteamento — {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"- Provedor/modelo: `{provider}` / `{model}`",
        f"- Acurácia: **{metrics['acuracia']:.1%}** ({metrics['acertos']}/{metrics['casos']}) · macro-F1 {metrics['macro_f1']:.3f}",
        f"- Latência p50/p95: {metrics['latencia_p50_ms']:.0f} / {metrics['latencia_p95_ms']:.0f} ms · tokens: {metrics['tokens_totais']}",
        f"- Erros de API: {metrics['erros_api']} · saídas sem rota legível: {metrics['saidas_invalidas']}"
        + (f" · acurácia só dos respondidos: {metrics['acuracia_respondidos']:.1%}" if metrics["erros_api"] else ""),
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
        extra = {"saida_invalida": " _(saída sem rota legível)_", "erro_api": f" _(API: {r.erro})_"}.get(r.status, "")
        lines.append(f"- `{r.id}` [{r.dificuldade}] esperado **{r.esperado}**, obtido **{r.obtido}**{extra} — {r.mensagem}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--limit", type=int, default=None, help="avaliar só os N primeiros casos")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--rpm", type=float, default=None, help="máx. de chamadas por minuto (ex.: 10 no Gemini free)")
    parser.add_argument("--retries", type=int, default=3, help="tentativas por caso em erro de API")
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
    results = asyncio.run(run_cases(cases, concurrency=args.concurrency, rpm=args.rpm, retries=args.retries))
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
