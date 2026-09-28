"""Lock de sessão entre réplicas (Redis SET NX PX + renovação + release em Lua).

Roda contra um redis-server de verdade: o binário local é iniciado numa porta
livre; sem ele, usa TEST_REDIS_URL; sem nenhum dos dois, os testes são pulados.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import time

import pytest
import pytest_asyncio

from src.services.session_lock import SessionBusy, SessionLockManager

redis_asyncio = pytest.importorskip("redis.asyncio")

pytestmark = pytest.mark.asyncio


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def redis_url():
    url = os.getenv("TEST_REDIS_URL")
    if url:
        yield url
        return
    binary = shutil.which("redis-server")
    if not binary:
        pytest.skip("redis-server indisponível e TEST_REDIS_URL não definida")
    port = _free_port()
    proc = subprocess.Popen(
        [binary, "--port", str(port), "--save", "", "--appendonly", "no"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    yield f"redis://127.0.0.1:{port}/15"
    proc.terminate()
    proc.wait(timeout=5)


@pytest_asyncio.fixture()
async def redis(redis_url):
    client = redis_asyncio.Redis.from_url(redis_url, decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


def _replica(redis, **kwargs) -> SessionLockManager:
    """Um SessionLockManager = uma réplica (locks locais próprios)."""
    kwargs.setdefault("ttl_seconds", 5)
    kwargs.setdefault("wait_seconds", 5)
    return SessionLockManager(redis_getter=lambda: redis, **kwargs)


async def _critical(manager, session_id, log, name, hold=0.15):
    async with manager.hold(session_id):
        log.append(("in", name, time.monotonic()))
        await asyncio.sleep(hold)
        log.append(("out", name, time.monotonic()))


def _max_overlap(log) -> int:
    current = peak = 0
    for kind, _name, _t in sorted(log, key=lambda item: item[2]):
        current += 1 if kind == "in" else -1
        peak = max(peak, current)
    return peak


# ------------------------------------------------------------- exclusão


async def test_same_session_is_serialized_across_replicas(redis) -> None:
    replicas = [_replica(redis) for _ in range(3)]
    log: list = []
    await asyncio.gather(*(_critical(r, "s1", log, f"r{i}") for i, r in enumerate(replicas)))
    assert _max_overlap(log) == 1
    assert await redis.exists("session:lock:s1") == 0


async def test_different_sessions_run_in_parallel(redis) -> None:
    a, b = _replica(redis), _replica(redis)
    log: list = []
    started = time.monotonic()
    await asyncio.gather(_critical(a, "s1", log, "a", 0.3), _critical(b, "s2", log, "b", 0.3))
    assert time.monotonic() - started < 0.55
    assert _max_overlap(log) == 2


async def test_waiter_gets_session_busy_after_wait_timeout(redis) -> None:
    holder, waiter = _replica(redis), _replica(redis, wait_seconds=0.3)
    entered = asyncio.Event()

    async def hold_long():
        async with holder.hold("s1"):
            entered.set()
            await asyncio.sleep(1.0)

    task = asyncio.create_task(hold_long())
    await entered.wait()
    t0 = time.monotonic()
    with pytest.raises(SessionBusy):
        async with waiter.hold("s1"):
            pytest.fail("não deveria entrar")
    assert 0.25 <= time.monotonic() - t0 < 0.8
    await task


async def test_same_replica_waiters_also_time_out(redis) -> None:
    """A espera na fila local também respeita o prazo."""
    manager = _replica(redis, wait_seconds=0.2)
    entered = asyncio.Event()

    async def hold_long():
        async with manager.hold("s1"):
            entered.set()
            await asyncio.sleep(0.6)

    task = asyncio.create_task(hold_long())
    await entered.wait()
    with pytest.raises(SessionBusy):
        async with manager.hold("s1"):
            pass
    await task
    assert len(manager.local) == 0


# ------------------------------------------------------ TTL e renovação


async def test_ttl_is_renewed_while_the_answer_is_generated(redis) -> None:
    holder = _replica(redis, ttl_seconds=1)
    other = _replica(redis, wait_seconds=0.1)
    async with holder.hold("s1"):
        await asyncio.sleep(2.2)  # > 2x o TTL
        assert await redis.exists("session:lock:s1") == 1
        with pytest.raises(SessionBusy):
            async with other.hold("s1"):
                pass
    assert await redis.exists("session:lock:s1") == 0


async def test_crashed_replica_is_released_by_ttl(redis) -> None:
    # Réplica que morreu segurando o lock: chave sem dono ativo e sem renovação.
    await redis.set("session:lock:s1", "token-do-pod-morto", px=600)
    survivor = _replica(redis, wait_seconds=2)
    t0 = time.monotonic()
    async with survivor.hold("s1"):
        waited = time.monotonic() - t0
    assert 0.3 < waited < 1.5


async def test_release_never_deletes_a_lock_owned_by_someone_else(redis) -> None:
    manager = _replica(redis)
    async with manager.hold("s1"):
        # Simula expiração + aquisição por outra réplica no meio do caminho.
        await redis.set("session:lock:s1", "outro-dono", px=5000)
    assert await redis.get("session:lock:s1") == "outro-dono"


async def test_cancelled_holder_still_releases(redis) -> None:
    """Cliente fecha o SSE → a tarefa é cancelada dentro do lock."""
    manager = _replica(redis)
    entered = asyncio.Event()

    async def hold_forever():
        async with manager.hold("s1"):
            entered.set()
            await asyncio.sleep(60)

    task = asyncio.create_task(hold_forever())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.05)
    assert await redis.exists("session:lock:s1") == 0
    assert len(manager.local) == 0


# ------------------------------------------------------- falhas e bordas


async def test_timeout_inside_the_body_is_not_reported_as_busy(redis) -> None:
    manager = _replica(redis)
    with pytest.raises(asyncio.TimeoutError):
        async with manager.hold("s1"):
            raise asyncio.TimeoutError("LLM demorou")
    assert await redis.exists("session:lock:s1") == 0


async def test_redis_down_fails_open_with_local_lock() -> None:
    dead = redis_asyncio.Redis(host="127.0.0.1", port=_free_port(), socket_connect_timeout=0.2)
    manager = SessionLockManager(redis_getter=lambda: dead, ttl_seconds=5, wait_seconds=2)
    log: list = []
    await asyncio.gather(*(_critical(manager, "s1", log, f"t{i}", 0.05) for i in range(3)))
    assert _max_overlap(log) == 1  # o lock local ainda serializa no processo
    await dead.aclose()


async def test_without_redis_uses_only_local_lock() -> None:
    manager = SessionLockManager(redis_getter=lambda: None, wait_seconds=2)
    log: list = []
    await asyncio.gather(*(_critical(manager, "s1", log, f"t{i}", 0.05) for i in range(3)))
    assert _max_overlap(log) == 1
    assert len(manager.local) == 0


# ---------------------------------------------- ponta a ponta no ChatService


async def _two_replicas(redis, *, distributed: bool):
    pytest.importorskip("langgraph")
    from src.agents.graph import EcoGraphRuntime
    from src.core.config import Settings
    from src.integrations.mcp.client import CalendarMcpClient
    from src.services.chat_service import ChatService
    from src.services.rag_service import RAGService
    from src.services.session_service import SessionService

    settings = Settings.from_env().with_overrides(
        environment="test",
        llm_provider="mock",
        embedding_provider="mock",
        storage_mode="memory",
        enable_external_source=False,
        postgres_url=None,
        rate_limit_per_minute=100,
    )

    class _Stub:
        client = None

    shared_storage: dict = {}  # faz o papel do MongoDB compartilhado
    rag = RAGService(settings)
    await rag.start()
    services = []
    for _ in range(2):
        sessions = SessionService(settings, _Stub(), _Stub())  # type: ignore[arg-type]
        sessions._memory_sessions = shared_storage
        sessions.locks = SessionLockManager(
            redis_getter=(lambda: redis) if distributed else (lambda: None),
            ttl_seconds=5,
            wait_seconds=10,
        )
        graph = EcoGraphRuntime(settings, sessions, rag, CalendarMcpClient(None, settings))
        services.append(ChatService(sessions, graph))
    return services


async def _race(services):
    from src.api.schemas.chat import ChatRequest
    from src.shared.context import UserContext

    user = UserContext(user_id=77, perfil="SINDICO_RESIDENCIAL", condominio_id=None)
    first = await services[0].chat(ChatRequest(mensagem="Oi"), request_id="r0", user_context=user)

    running = peak = 0
    for service in services:
        original = service.graph.invoke

        async def slow_invoke(state, _original=original):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            try:
                await asyncio.sleep(0.2)
                return await _original(state)
            finally:
                running -= 1

        service.graph.invoke = slow_invoke

    await asyncio.gather(
        services[0].chat(ChatRequest(session_id=first.session_id, mensagem="A"), request_id="rA", user_context=user),
        services[1].chat(ChatRequest(session_id=first.session_id, mensagem="B"), request_id="rB", user_context=user),
    )
    stored = await services[0].sessions.get_session(first.session_id, 77)
    return peak, [m["role"] for m in stored["messages"]]


async def test_two_replicas_same_session_are_serialized_end_to_end(redis) -> None:
    peak, roles = await _race(await _two_replicas(redis, distributed=True))
    assert peak == 1
    assert roles == ["user", "assistant"] * 3


async def test_local_lock_alone_does_not_protect_across_replicas(redis) -> None:
    """Controle: com o lock antigo (só local), as réplicas processam juntas e a
    memória intercala user/user/assistant/assistant. É o bug que o Redis cobre."""
    peak, roles = await _race(await _two_replicas(redis, distributed=False))
    assert peak == 2
    assert roles != ["user", "assistant"] * 3


# ------------------------------------------------ achados da revisão independente


async def test_zero_wait_acquires_a_free_lock_and_rejects_a_busy_one(redis) -> None:
    """wait_for(timeout=0) cancelava antes de tentar: todo /chat virava 409."""
    manager = _replica(redis, wait_seconds=0)
    async with manager.hold("s1"):
        other = _replica(redis, wait_seconds=0)
        with pytest.raises(SessionBusy):
            async with other.hold("s1"):
                pass
        with pytest.raises(SessionBusy):  # mesma réplica, lock local ocupado
            async with manager.hold("s1"):
                pass
    async with SessionLockManager(redis_getter=lambda: None, wait_seconds=0).hold("s2"):
        pass


async def test_cancellation_during_cleanup_is_not_swallowed() -> None:
    class SlowRenewRedis:
        def __init__(self):
            self.store = {}

        async def set(self, key, value, nx, px):
            if key in self.store:
                return None
            self.store[key] = value
            return True

        async def eval(self, script, numkeys, key, token, *args):
            if "pexpire" in script:
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    await asyncio.sleep(0.2)  # redis-py desconecta ao ser cancelado
                    raise
                return 1
            if self.store.get(key) == token:
                del self.store[key]
                return 1
            return 0

    fake = SlowRenewRedis()
    manager = SessionLockManager(redis_getter=lambda: fake, ttl_seconds=1, wait_seconds=5)
    depois: list[str] = []

    async def request():
        async with manager.hold("s1"):
            await asyncio.sleep(0.4)  # a renovação está dentro do eval
        depois.append("continuou depois do cancel")

    task = asyncio.create_task(request())
    await asyncio.sleep(0.45)  # a limpeza está esperando a renovação terminar
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert depois == []
    assert fake.store == {}  # e o lock foi liberado mesmo assim
