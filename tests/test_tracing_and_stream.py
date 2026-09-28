"""Mascaramento de PII no tracing, consumo de tokens e SSE do /chat/stream."""

from __future__ import annotations

import json

import pytest

from src.core.config import Settings
from src.observability.tracing import (
    TracingService,
    conversation_trace,
    current_trace,
    mask_pii,
    mask_text,
    record_usage,
)


# ------------------------------------------------------------------ PII


@pytest.mark.parametrize(
    ("raw", "must_not_contain", "placeholder"),
    [
        ("meu cpf é 123.456.789-09", "123.456.789-09", "[CPF]"),
        ("cpf 12345678909 ok", "12345678909", "[CPF]"),
        ("CNPJ 12.345.678/0001-95", "12.345.678/0001-95", "[CNPJ]"),
        ("fale com joao.silva+eco@gmail.com", "joao.silva+eco@gmail.com", "[EMAIL]"),
        ("ligue (11) 98765-4321", "98765-4321", "[TELEFONE]"),
        ("CEP 01310-100", "01310-100", "[CEP]"),
        ("Authorization: Bearer abc.def-123", "abc.def-123", "[TOKEN]"),
        ("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig_abc", "eyJhbGciOiJIUzI1NiJ9", "[JWT]"),
    ],
)
def test_mask_text_removes_pii(raw: str, must_not_contain: str, placeholder: str) -> None:
    masked = mask_text(raw)
    assert must_not_contain not in masked
    assert placeholder in masked


@pytest.mark.parametrize(
    ("raw", "must_not_contain", "placeholder"),
    [
        ("celular 11912345678", "11912345678", "[TELEFONE]"),
        ("+55 11 91234-5678", "91234-5678", "[TELEFONE]"),
        ("RG 12.345.678-9", "12.345.678-9", "[RG]"),
        ("cnpj 11222333000181", "11222333000181", "[CNPJ]"),
        ("cpf 52998224725", "52998224725", "[CPF]"),
    ],
)
def test_mask_text_other_brazilian_formats(raw: str, must_not_contain: str, placeholder: str) -> None:
    masked = mask_text(raw)
    assert must_not_contain not in masked
    assert placeholder in masked


@pytest.mark.parametrize(
    "text",
    [
        "Você reciclou 12 kg em 3 coletas no ciclo 2026",
        "timestamp 1727450000 e id 1234567890",       # 10 dígitos soltos
        "pedido 12345678901",                          # 11 dígitos sem DV de CPF nem cara de celular
        "coleta em 27/09/2026 às 08:00",
        "request 5f0c7c2e-8a1b-4c3d-9e2f-1a2b3c4d5e6f",
        "R$ 1.234,56",
    ],
)
def test_mask_keeps_ordinary_numbers(text: str) -> None:
    assert mask_text(text) == text


def test_mask_pii_is_recursive_and_redacts_sensitive_keys() -> None:
    data = {
        "messages": [{"role": "user", "content": "cpf 123.456.789-09"}],
        "token": "segredo",
        "meta": ("email a@b.com", 42),
    }
    masked = mask_pii(data)
    assert masked["token"] == "[REDACTED]"
    assert masked["messages"][0]["content"] == "cpf [CPF]"
    assert masked["meta"] == ("email [EMAIL]", 42)


# ------------------------------------------------------------- provedor


def test_tracing_is_off_by_default_and_in_mock() -> None:
    settings = Settings(environment="test", llm_provider="mock", tracing_provider="langfuse")
    service = TracingService(settings)
    assert service.enabled is False
    assert service.run_config("faq") == {}


def test_tracing_misconfiguration_never_raises() -> None:
    settings = Settings(environment="test", llm_provider="ollama", tracing_provider="langfuse")
    service = TracingService(settings)  # sem chaves
    assert service.enabled is False


def test_langfuse_config_carries_session_and_user() -> None:
    pytest.importorskip("langfuse")
    settings = Settings(
        environment="test",
        llm_provider="ollama",
        tracing_provider="langfuse",
        langfuse_public_key="pk-lf-test",
        langfuse_secret_key="sk-lf-test",
        langfuse_host="http://127.0.0.1:9",
    )
    service = TracingService(settings)
    try:
        assert service.enabled is True
        with conversation_trace(session_id="s-1", user_id=7, request_id="r-1", perfil="USUARIO_COMUM"):
            config = service.run_config("orquestrador")
        assert config["metadata"]["langfuse_session_id"] == "s-1"
        assert config["metadata"]["langfuse_user_id"] == "7"
        assert "agent:orquestrador" in config["tags"]
        assert len(config["callbacks"]) == 1
    finally:
        service.shutdown()


# --------------------------------------------------------------- tokens


class _AIMessage:
    type = "ai"

    def __init__(self, input_tokens: int, output_tokens: int):
        self.usage_metadata = {"input_tokens": input_tokens, "output_tokens": output_tokens}


class _HumanMessage:
    type = "human"
    usage_metadata = {"input_tokens": 999, "output_tokens": 999}


def test_record_usage_accumulates_per_conversation() -> None:
    with conversation_trace(session_id="s", user_id=1, request_id="r", perfil="X") as trace:
        record_usage(provider="ollama", agent="faq", messages=[_HumanMessage(), _AIMessage(100, 20)])
        record_usage(provider="ollama", agent="juiz_saida", messages=[_AIMessage(50, 5)])
    assert trace.usage.as_dict() == {"input_tokens": 150, "output_tokens": 25, "total_tokens": 175}
    assert current_trace() is None


def test_chat_response_exposes_usage(client) -> None:
    response = client.post(
        "/api/v1/chat",
        headers={"X-Usuario-Id": "42", "X-Perfil": "morador"},
        json={"mensagem": "Como separar resíduos recicláveis?"},
    )
    body = response.json()
    assert response.status_code == 200
    # modo mock não consome tokens, mas o contrato do campo existe
    assert body["usage"] == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    assert body["judge"]["categoria"] == "aprovado"


# ------------------------------------------------------------------- SSE


def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    events = []
    for block in raw.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_chat_stream_emits_progress_then_validated_answer(client) -> None:
    with client.stream(
        "POST",
        "/api/v1/chat/stream",
        headers={"X-Usuario-Id": "43", "X-Perfil": "morador"},
        json={"mensagem": "Como separar resíduos recicláveis?"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        raw = "".join(response.iter_text())

    events = _parse_sse(raw)
    names = [name for name, _ in events]
    assert names[0] == "session"
    assert names[-2:] == ["answer", "done"]
    progress_steps = [data["step"] for name, data in events if name == "progress"]
    assert "orquestrador" in progress_steps
    assert "juiz_saida" in progress_steps  # a resposta só sai depois do juiz
    assert progress_steps.index("juiz_saida") < len(progress_steps)

    done = events[-1][1]
    answer = events[-2][1]["answer"]
    assert done["answer"] == answer
    assert done["agent"] == "educacional"
    assert done["session_id"] == events[0][1]["session_id"]

    # A resposta ficou persistida na memória, igual ao /chat.
    stored = client.app.state.sessions._memory_sessions[done["session_id"]]["messages"]
    assert stored[-1]["role"] == "assistant"
    assert stored[-1]["content"] == answer


def test_chat_stream_limit_is_plain_429_before_stream(client) -> None:
    client.app.state.quotas.settings = client.app.state.quotas.settings.with_overrides(quota_usuario_comum=1)
    headers = {"X-Usuario-Id": "44", "X-Perfil": "comum"}
    first = client.post("/api/v1/chat", headers=headers, json={"mensagem": "Oi"})
    assert first.status_code == 200
    blocked = client.post("/api/v1/chat/stream", headers=headers, json={"mensagem": "Oi"})
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["codigo"] == "limite_daily"


@pytest.mark.asyncio
async def test_stream_closed_from_another_context_releases_everything_cleanly() -> None:
    """Cliente fecha o SSE no meio: o gerador é finalizado depois, em outro
    Context. Antes, o reset do ContextVar do token levantava ValueError, que
    virava "erro" + reembolso e `RuntimeError: async generator ignored
    GeneratorExit`, e o stream do grafo ficava com tarefas órfãs."""
    import asyncio
    import contextvars

    pytest.importorskip("langgraph")
    from src.agents.graph import EcoGraphRuntime
    from src.api.schemas.chat import ChatRequest
    from src.integrations.mcp.client import CalendarMcpClient
    from src.services.chat_service import ChatService
    from src.services.quota_service import QuotaService
    from src.services.rag_service import RAGService
    from src.services.session_service import SessionService
    from src.shared.context import UserContext

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

    sessions = SessionService(settings, _Stub(), _Stub())  # type: ignore[arg-type]
    rag = RAGService(settings)
    await rag.start()
    quotas = QuotaService(settings)
    service = ChatService(sessions, EcoGraphRuntime(settings, sessions, rag, CalendarMcpClient(None, settings)), quotas=quotas)
    user = UserContext(user_id=7, perfil="USUARIO_COMUM", condominio_id=None, token="tok")
    request = ChatRequest(mensagem="Oi")
    session, quota_status = await service.admit(request, user)

    gen = service.stream(request, request_id="r", user_context=user, session=session, quota_status=quota_status)
    events = []
    async for event, _ in gen:
        events.append(event)
        if event == "progress":
            break
    assert len(sessions.locks.local) == 1

    task = contextvars.Context().run(asyncio.get_running_loop().create_task, gen.aclose())
    await task  # não levanta RuntimeError
    assert len(sessions.locks.local) == 0
    # A pergunta foi processada até onde o cliente ficou: a cota não é devolvida
    # só porque ele fechou a conexão (senão abrir e fechar o stream seria grátis).
    assert (await quotas.status(user)).used == 1
    await asyncio.sleep(0.05)  # finalizadores de async generators rodam no loop
    pendentes = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    assert pendentes == []
