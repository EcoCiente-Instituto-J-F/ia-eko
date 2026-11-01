from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time

import httpx


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * p
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


PERGUNTA_PADRAO = {"mensagem": "Como separar resíduos recicláveis?", "perfil": "morador"}


async def run_one(
    client: httpx.AsyncClient,
    url: str,
    user_id: int,
    token: str | None,
    pergunta: dict = PERGUNTA_PADRAO,
) -> tuple[float, bool]:
    started = time.perf_counter()
    headers = (
        {"Authorization": f"Bearer {token}"}
        if token
        else {"X-Usuario-Id": str(user_id), "X-Perfil": pergunta["perfil"], "X-Condominio-Id": "1"}
    )
    payload = {
        "session_id": None,
        "mensagem": pergunta["mensagem"],
    }
    if not token:
        payload["usuario_id"] = user_id
    try:
        response = await client.post(url, headers=headers, json=payload)
        ok = response.is_success
    except httpx.HTTPError:
        ok = False
    return (time.perf_counter() - started) * 1000, ok


async def benchmark(
    base_url: str, requests: int, concurrency: int, token: str | None, perguntas: list[dict], primeiro_id: int
) -> None:
    url = f"{base_url.rstrip('/')}/api/v1/chat"
    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(timeout=120.0) as client:
        async def guarded(i: int):
            async with semaphore:
                return await run_one(client, url, primeiro_id + i, token, perguntas[i % len(perguntas)])

        started = time.perf_counter()
        results = await asyncio.gather(*(guarded(i) for i in range(requests)))
        elapsed = time.perf_counter() - started

    latencies = [lat for lat, _ in results]
    failures = sum(1 for _, ok in results if not ok)
    print(f"requests={requests}")
    print(f"concurrency={concurrency}")
    print(f"mean_ms={statistics.fmean(latencies):.2f}")
    print(f"median_ms={statistics.median(latencies):.2f}")
    print(f"p95_ms={percentile(latencies, 0.95):.2f}")
    print(f"p99_ms={percentile(latencies, 0.99):.2f}")
    print(f"error_rate={failures / requests:.4%}")
    print(f"throughput_rps={requests / elapsed:.2f}")
    print(f"elapsed_s={elapsed:.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark da API EcoCiente em modo mock/local.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument(
        "--token",
        default=None,
        help="Bearer token real. Sem token, o benchmark exige APP_ENV=test e ALLOW_TEST_IDENTITY_HEADERS=true.",
    )
    parser.add_argument(
        "--perguntas",
        default=None,
        help='JSON com [{"mensagem": ..., "perfil": ...}], usadas em rodízio. Sem ele, uma pergunta fixa.',
    )
    parser.add_argument("--primeiro-id", type=int, default=10_000, help="usuario_id sintético da primeira requisição")
    args = parser.parse_args()
    if args.requests <= 0 or args.concurrency <= 0:
        parser.error("requests e concurrency devem ser > 0")
    if not args.token:
        print("AVISO: usando identidade sintética; a API precisa estar em APP_ENV=test com ALLOW_TEST_IDENTITY_HEADERS=true.")
    perguntas = [PERGUNTA_PADRAO]
    if args.perguntas:
        with open(args.perguntas, encoding="utf-8") as arquivo:
            perguntas = json.load(arquivo)
    asyncio.run(benchmark(args.base_url, args.requests, args.concurrency, args.token, perguntas, args.primeiro_id))


if __name__ == "__main__":
    main()
