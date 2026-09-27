from __future__ import annotations

import httpx
import pytest

from src.integrations.auth.client import AuthApiClient
from src.integrations.calendar.schemas import Agendamento, BuscarProximaColetaToolResult, CalendarStatus
from src.integrations.mcp.client import McpToolProtocolError
from src.security.authentication import AuthenticationService, AuthenticationUnavailable
from src.shared.context import UserContext


class StubAuthenticationService:
    def __init__(self, user: UserContext):
        self.user = user
        self.tokens: list[str] = []

    async def authenticate(self, token: str) -> UserContext:
        self.tokens.append(token)
        return self.user

    async def close(self) -> None:
        return None


class FakeCalendarTools:
    """Substitui CalendarTools nos testes: mesma interface (next_collection/list_collections),
    sem depender de MCP, HTTP ou Authorization header."""

    def __init__(self, result: BuscarProximaColetaToolResult | None = None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls: list[tuple[int | None, str | None]] = []

    async def next_collection(self, user: UserContext, **filters) -> BuscarProximaColetaToolResult:
        self.calls.append(user.condominio_id)
        if self.error is not None:
            raise self.error
        return self.result


def _set_authentication(client, user: UserContext) -> StubAuthenticationService:
    service = StubAuthenticationService(user)
    client.app.state.authentication = service
    return service


@pytest.mark.asyncio
async def test_authentication_api_builds_user_context() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer jwt_usuario"
        return httpx.Response(
            200,
            json={
                "id": 123,
                "nome": "João",
                "perfil": "SINDICO_RESIDENCIAL",
                "condominio_id": 55,
                "permissoes": ["analytics", "coleta", "ranking"],
            },
        )

    api_client = AuthApiClient(
        "https://auth.example/me",
        retries=0,
        transport=httpx.MockTransport(handler),
    )
    service = AuthenticationService(api_client)
    context = await service.authenticate("jwt_usuario")

    assert context.user_id == 123
    assert context.perfil == "SINDICO_RESIDENCIAL"
    assert context.condominio_id == 55
    assert context.permissoes == ["analytics", "coleta", "ranking"]
    assert "token" not in context.for_agent()
    await service.close()


@pytest.mark.asyncio
async def test_authentication_api_failure_is_safe() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    api_client = AuthApiClient(
        "https://auth.example/me",
        retries=0,
        transport=httpx.MockTransport(handler),
    )
    service = AuthenticationService(api_client)

    with pytest.raises(AuthenticationUnavailable):
        await service.authenticate("jwt_usuario")
    await service.close()


def test_chat_receives_authenticated_profile(client) -> None:
    auth = _set_authentication(
        client,
        UserContext(
            user_id=123,
            perfil="SINDICO_RESIDENCIAL",
            condominio_id=55,
            permissoes=["analytics", "coleta", "ranking"],
            token="jwt_usuario",
        ),
    )

    response = client.post(
        "/api/v1/chat",
        json={"token": "jwt_usuario", "session_id": None, "mensagem": "Qual material foi mais reciclado?"},
    )

    assert response.status_code == 200
    assert response.json()["agent"] == "analytics"
    assert auth.tokens == ["jwt_usuario"]
    session_id = response.json()["session_id"]
    assert client.app.state.sessions._memory_sessions[session_id]["usuario_id"] == 123


def test_resident_is_blocked_from_macro_ranking(client) -> None:
    _set_authentication(
        client,
        UserContext(
            user_id=9,
            perfil="MORADOR_RESIDENCIAL",
            condominio_id=55,
            permissoes=["analytics"],
            token="jwt_morador",
        ),
    )

    response = client.post(
        "/api/v1/chat",
        json={"token": "jwt_morador", "mensagem": "Mostre o ranking de todos os condomínios"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["agent"] == "autorizacao"
    assert "análise individual" in body["answer"]
    assert "analytics" not in body["agents_called"]


def test_collection_agent_calls_calendar_after_router(client) -> None:
    _set_authentication(
        client,
        UserContext(
            user_id=77,
            perfil="SINDICO_RESIDENCIAL",
            condominio_id=55,
            permissoes=["coleta"],
            token="jwt_calendar",
        ),
    )
    fake_tools = FakeCalendarTools(
        result=BuscarProximaColetaToolResult(
            ok=True,
            found=True,
            agendamento=Agendamento(
                id=1,
                condominioId=55,
                cooperativaId=1,
                dataInicio="2030-01-20T08:00:00",
                statusAgendamento=CalendarStatus.CONFIRMADO,
                possuiRecorrencia=False,
            ),
        )
    )
    client.app.state.graph.collection_agent.tools = fake_tools

    response = client.post(
        "/api/v1/chat",
        json={"token": "jwt_calendar", "mensagem": "Quando será minha próxima coleta?"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["agent"] == "coletas"
    assert "orquestrador" in body["agents_called"]
    assert "coletas" in body["agents_called"]
    assert fake_tools.calls == [55]
    assert "20/01/2030" in body["answer"]


def test_collection_agent_does_not_invent_dates_when_calendar_fails(client) -> None:
    _set_authentication(
        client,
        UserContext(
            user_id=77,
            perfil="SINDICO_RESIDENCIAL",
            condominio_id=55,
            permissoes=["coleta"],
            token="jwt_calendar",
        ),
    )
    client.app.state.graph.collection_agent.tools = FakeCalendarTools(
        error=McpToolProtocolError("calendar offline")
    )

    response = client.post(
        "/api/v1/chat",
        json={"token": "jwt_calendar", "mensagem": "Quando será minha próxima coleta?"},
    )

    assert response.status_code == 200
    assert "Não consegui executar a consulta ao calendário" in response.json()["answer"]
    assert "20/01/2030" not in response.json()["answer"]
