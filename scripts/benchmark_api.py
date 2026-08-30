from __future__ import annotations

import argparse
import asyncio
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


async def run_one(
    client: httpx.AsyncClient,
    url: str,
    user_id: int,
    token: str | None,
) -> tuple[float, bool]:
    started = time.perf_counter()
    headers = (
        {"Authorization": f"Bearer {token}"}
        if token
        else {"X-Usuario-Id": str(user_id), "X-Perfil": "morador"}
    )
    payload = {
        "session_id": None,
        "mensagem": "Como separar resíduos recicláveis?",
    }
    if not token:
        payload["usuario_id"] = user_id
    try:
        response = await client.post(url, headers=headers, json=payload)
        ok = response.is_success
    except httpx.HTTPError:
        ok = False
    return (time.perf_counter() - started) * 1000, ok


async def benchmark(base_url: str, requests: int, concurrency: int, token: str | None) -> None:
    url = f"{base_url.rstrip('/')}/api/v1/chat"
    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(timeout=60.0) as client:
        async def guarded(i: int):
            async with semaphore:
                return await run_one(client, url, 10_000 + i, token)

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
