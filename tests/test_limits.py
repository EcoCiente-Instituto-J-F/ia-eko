"""Cota diária por perfil, anti-rajada e teto de sessão sem resumo de memória."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.core.config import Settings
from src.services.quota_service import QuotaExceeded, QuotaService
from src.shared.context import UserContext

TZ = ZoneInfo("America/Sao_Paulo")


def _settings(**overrides) -> Settings:
    base = dict(
        environment="test",
        llm_provider="mock",
        embedding_provider="mock",
        storage_mode="memory",
        allow_storage_fallback=True,
        enable_external_source=False,
        postgres_url=None,
        allow_test_identity_headers=True,
    )
    base.update(overrides)
    return Settings.from_env().with_overrides(**base)


@pytest.fixture()
def make_client():
    pytest.importorskip("langgraph")
    from fastapi.testclient import TestClient

    from src.api.main import create_app

    clients = []

    def factory(**overrides):
        client = TestClient(create_app(_settings(**overrides)))
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


def _headers(user_id: int, perfil: str) -> dict[str, str]:
    return {"X-Usuario-Id": str(user_id), "X-Perfil": perfil}


def _user(perfil: str, user_id: int = 1) -> UserContext:
    return UserContext(user_id=user_id, perfil=perfil, condominio_id=None)


# ---------------------------------------------------------------- unidade


def test_default_daily_limits_per_profile() -> None:
    quotas = QuotaService(_settings())
    assert quotas.daily_limit("USUARIO_COMUM") == 20
    assert quotas.daily_limit("MORADOR_RESIDENCIAL") == 40
    assert quotas.daily_limit("USUARIO_COMERCIAL") == 50
    assert quotas.daily_limit("COOPERATIVA") == 80
    assert quotas.daily_limit("SINDICO_RESIDENCIAL") == 100
    assert quotas.daily_limit("SINDICO_COMERCIAL") == 100


def test_unknown_profile_gets_most_restrictive_limit_not_unlimited() -> None:
    assert QuotaService(_settings()).daily_limit("PERFIL_INVENTADO") == 20


def test_zero_means_unlimited() -> None:
    assert QuotaService(_settings(quota_sindico_residencial=0)).daily_limit("SINDICO_RESIDENCIAL") is None


def test_only_comum_has_session_cap_by_default() -> None:
    quotas = QuotaService(_settings())
    assert quotas.session_message_cap("USUARIO_COMUM") == 20
    assert quotas.session_message_cap("SINDICO_RESIDENCIAL") is None


@pytest.mark.asyncio
async def test_daily_quota_blocks_after_limit_and_refund_restores() -> None:
    quotas = QuotaService(_settings(quota_usuario_comum=2))
    user = _user("USUARIO_COMUM")
    await quotas.reserve_daily(user)
    second = await quotas.reserve_daily(user)
    with pytest.raises(QuotaExceeded) as exc:
        await quotas.reserve_daily(user)
    assert exc.value.kind == "daily"
    assert exc.value.reset_at is not None
    # A tentativa recusada não conta; o reembolso libera 1 vaga.
    assert (await quotas.status(user)).used == 2
    await quotas.refund_daily(second)
    await quotas.reserve_daily(user)


@pytest.mark.asyncio
async def test_refund_after_midnight_returns_to_the_reserved_day() -> None:
    quotas = QuotaService(_settings(quota_usuario_comum=5))
    user = _user("USUARIO_COMUM")
    before = datetime(2026, 9, 27, 23, 59, 59, tzinfo=TZ)
    quotas._now_fn = lambda: before
    reserved = await quotas.reserve_daily(user)
    quotas._now_fn = lambda: before + timedelta(seconds=2)  # a requisição falha já no dia 28
    await quotas.refund_daily(reserved)
    assert (await quotas.status(user)).used == 0  # dia 28 intacto (sem contador negativo)
    quotas._now_fn = lambda: before
    assert (await quotas.status(user)).used == 0  # dia 27 devolvido


class _FlakyRedis:
    """INCR funciona, EXPIRE falha: não pode contar duas vezes."""

    def __init__(self):
        self.values: dict[str, int] = {}

    async def incr(self, key):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key, ttl):
        raise ConnectionError("redis caiu no meio")

    async def get(self, key):
        return self.values.get(key)

    async def decr(self, key):
        self.values[key] -= 1


@pytest.mark.asyncio
async def test_expire_failure_does_not_double_count() -> None:
    class _Db:
        client = _FlakyRedis()

    quotas = QuotaService(_settings(quota_usuario_comum=5), _Db())
    user = _user("USUARIO_COMUM")
    status = await quotas.reserve_daily(user)
    assert status.used == 1
    assert quotas._memory == {}


@pytest.mark.asyncio
async def test_daily_quota_resets_at_local_midnight() -> None:
    quotas = QuotaService(_settings(quota_usuario_comum=1))
    user = _user("USUARIO_COMUM")
    day = datetime(2026, 9, 27, 23, 59, tzinfo=TZ)
    quotas._now_fn = lambda: day
    await quotas.reserve_daily(user)
    with pytest.raises(QuotaExceeded):
        await quotas.reserve_daily(user)
    quotas._now_fn = lambda: day + timedelta(minutes=2)  # 00:01 do dia seguinte em SP
    status = await quotas.reserve_daily(user)
    assert status.used == 1


@pytest.mark.asyncio
async def test_rate_limit_per_minute_window() -> None:
    quotas = QuotaService(_settings(rate_limit_per_minute=2))
    quotas._clock = lambda: 1_000_020.0
    user = _user("SINDICO_RESIDENCIAL")
    await quotas.check_rate(user)
    await quotas.check_rate(user)
    with pytest.raises(QuotaExceeded) as exc:
        await quotas.check_rate(user)
    assert exc.value.kind == "rate"
    assert 1 <= exc.value.retry_after <= 60
    quotas._clock = lambda: 1_000_020.0 + 60  # próxima janela
    await quotas.check_rate(user)


def test_session_cap_stops_before_compaction_threshold() -> None:
    quotas = QuotaService(_settings(memory_max_messages=4, memory_keep_recent_messages=2))
    user = _user("USUARIO_COMUM")
    three = {"messages": [{"role": "user"}, {"role": "assistant"}, {"role": "user"}]}
    four = {"messages": three["messages"] + [{"role": "assistant"}]}
    quotas.check_session(user, three)  # 3 + 1 = 4 → não passa de 4, permitido
    with pytest.raises(QuotaExceeded) as exc:
        quotas.check_session(user, four)  # 4 + 1 = 5 > 4 → acionaria o resumo
    assert exc.value.kind == "session"
    quotas.check_session(_user("SINDICO_RESIDENCIAL"), four)  # síndico pode resumir


# ---------------------------------------------------------------- HTTP


def test_http_daily_quota_returns_429_with_headers(make_client) -> None:
    client = make_client(quota_usuario_comum=2, rate_limit_per_minute=100)
    headers = _headers(501, "comum")
    for _ in range(2):
        ok = client.post("/api/v1/chat", headers=headers, json={"mensagem": "Como funciona o EcoCiente?"})
        assert ok.status_code == 200
        assert ok.json()["quota"]["limite_diario"] == 2
    blocked = client.post("/api/v1/chat", headers=headers, json={"mensagem": "Mais uma?"})
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["codigo"] == "limite_daily"
    assert blocked.headers["X-RateLimit-Limit"] == "2"
    assert int(blocked.headers["Retry-After"]) > 0

    quota = client.get("/api/v1/chat/quota", headers=headers).json()
    assert quota["usadas_hoje"] == 2
    assert quota["restantes_hoje"] == 0


def test_http_quota_is_per_user(make_client) -> None:
    client = make_client(quota_usuario_comum=1, rate_limit_per_minute=100)
    assert client.post("/api/v1/chat", headers=_headers(601, "comum"), json={"mensagem": "Oi"}).status_code == 200
    assert client.post("/api/v1/chat", headers=_headers(602, "comum"), json={"mensagem": "Oi"}).status_code == 200


def test_http_comum_session_never_triggers_memory_summarizer(make_client) -> None:
    client = make_client(memory_max_messages=4, memory_keep_recent_messages=2, rate_limit_per_minute=100)
    headers = _headers(701, "comum")
    first = client.post("/api/v1/chat", headers=headers, json={"mensagem": "Como funciona o EcoCiente?"})
    session_id = first.json()["session_id"]
    second = client.post("/api/v1/chat", headers=headers, json={"session_id": session_id, "mensagem": "E os pontos?"})
    assert second.status_code == 200
    for body in (first.json(), second.json()):
        assert "resumir_memoria" not in body["agents_called"]

    third = client.post("/api/v1/chat", headers=headers, json={"session_id": session_id, "mensagem": "E o ranking?"})
    assert third.status_code == 429
    assert third.json()["detail"]["codigo"] == "limite_session"

    # Uma nova sessão continua funcionando (a cota diária é que limita o total).
    fresh = client.post("/api/v1/chat", headers=headers, json={"mensagem": "Nova conversa"})
    assert fresh.status_code == 200


def test_http_sindico_session_can_compact(make_client) -> None:
    client = make_client(memory_max_messages=4, memory_keep_recent_messages=2, rate_limit_per_minute=100)
    headers = _headers(702, "sindico")
    session_id = client.post("/api/v1/chat", headers=headers, json={"mensagem": "Como funciona o EcoCiente?"}).json()["session_id"]
    agents = []
    for text in ("E os pontos?", "E as coletas?"):
        response = client.post("/api/v1/chat", headers=headers, json={"session_id": session_id, "mensagem": text})
        assert response.status_code == 200
        agents.extend(response.json()["agents_called"])
    assert "resumir_memoria" in agents


def test_http_rate_limit(make_client) -> None:
    client = make_client(rate_limit_per_minute=2)
    client.app.state.quotas._clock = lambda: 2_000_010.0
    headers = _headers(801, "sindico")
    for _ in range(2):
        assert client.post("/api/v1/chat", headers=headers, json={"mensagem": "Oi"}).status_code == 200
    blocked = client.post("/api/v1/chat", headers=headers, json={"mensagem": "Oi"})
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["codigo"] == "limite_rate"


def test_http_server_error_refunds_daily_quota(make_client) -> None:
    client = make_client(quota_usuario_comum=1, rate_limit_per_minute=100)
    graph = client.app.state.graph
    original = graph.invoke

    async def boom(_state):
        raise RuntimeError("falha simulada do grafo")

    graph.invoke = boom
    headers = _headers(901, "comum")
    with pytest.raises(RuntimeError):
        client.post("/api/v1/chat", headers=headers, json={"mensagem": "Oi"})
    graph.invoke = original
    # A cota de 1 resposta não foi consumida pela falha.
    assert client.post("/api/v1/chat", headers=headers, json={"mensagem": "Oi"}).status_code == 200


@pytest.mark.asyncio
async def test_concurrent_requests_cannot_bypass_session_cap() -> None:
    """Força o intercalamento do bug: A e B passam por `admit` (fora do lock)
    vendo a sessão com 2 mensagens; A roda e deixa 4; B só pega o lock depois.
    Sem a revalidação dentro do lock, B acionaria o resumo de memória."""
    import asyncio

    pytest.importorskip("langgraph")
    from src.agents.graph import EcoGraphRuntime
    from src.api.schemas.chat import ChatRequest
    from src.integrations.mcp.client import CalendarMcpClient
    from src.services.chat_service import ChatService
    from src.services.rag_service import RAGService
    from src.services.session_service import SessionService

    settings = _settings(memory_max_messages=4, memory_keep_recent_messages=2, rate_limit_per_minute=100)

    class _Stub:
        client = None

    sessions = SessionService(settings, _Stub(), _Stub())  # type: ignore[arg-type]
    rag = RAGService(settings)
    await rag.start()
    graph = EcoGraphRuntime(settings, sessions, rag, CalendarMcpClient(None, settings))
    quotas = QuotaService(settings)
    service = ChatService(sessions, graph, quotas=quotas)
    user = _user("USUARIO_COMUM", user_id=1001)

    first = await service.chat(ChatRequest(mensagem="Oi"), request_id="r0", user_context=user)
    session_id = first.session_id

    both_admitted = asyncio.Event()
    admitted = 0
    original_admit = service.admit

    async def admit_and_wait(request, user_context):
        nonlocal admitted
        result = await original_admit(request, user_context)
        admitted += 1
        if admitted == 2:
            both_admitted.set()
        await both_admitted.wait()
        return result

    service.admit = admit_and_wait
    results = await asyncio.gather(
        service.chat(ChatRequest(session_id=session_id, mensagem="A"), request_id="rA", user_context=user),
        service.chat(ChatRequest(session_id=session_id, mensagem="B"), request_id="rB", user_context=user),
        return_exceptions=True,
    )

    ok = [r for r in results if not isinstance(r, Exception)]
    blocked = [r for r in results if isinstance(r, QuotaExceeded)]
    assert len(ok) == 1 and len(blocked) == 1
    assert blocked[0].kind == "session"
    assert "resumir_memoria" not in ok[0].agents_called
    stored = await sessions.get_session(session_id, 1001)
    assert len(stored["messages"]) == 4  # a mensagem barrada nem foi gravada
    assert (await quotas.status(user)).used == 2  # e a cota dela foi devolvida


def test_http_busy_session_returns_409_and_refunds_quota(make_client) -> None:
    """Sessão presa em outra réplica além do SESSION_LOCK_WAIT_SECONDS."""
    from contextlib import asynccontextmanager

    from src.services.session_lock import SessionBusy

    client = make_client(quota_usuario_comum=5, rate_limit_per_minute=100)
    headers = _headers(4242, "comum")
    first = client.post("/api/v1/chat", headers=headers, json={"mensagem": "Oi"}).json()
    sessions = client.app.state.sessions

    @asynccontextmanager
    async def busy(session_id):
        raise SessionBusy(session_id, 45.0)
        yield  # pragma: no cover

    original = sessions.conversation_guard
    sessions.conversation_guard = busy
    try:
        resp = client.post("/api/v1/chat", headers=headers, json={"session_id": first["session_id"], "mensagem": "De novo"})
        assert resp.status_code == 409
        assert resp.json()["detail"]["codigo"] == "sessao_ocupada"
        assert resp.headers["Retry-After"] == "2"

        with client.stream(
            "POST", "/api/v1/chat/stream", headers=headers, json={"session_id": first["session_id"], "mensagem": "De novo"}
        ) as stream:
            body = "".join(stream.iter_text())
        assert "event: error" in body and "sessao_ocupada" in body
    finally:
        sessions.conversation_guard = original

    # Só a primeira resposta consumiu cota; as duas barradas foram devolvidas.
    assert client.get("/api/v1/chat/quota", headers=headers).json()["usadas_hoje"] == 1
