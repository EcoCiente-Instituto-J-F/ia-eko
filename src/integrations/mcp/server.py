from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator

from mcp.server import MCPServer
from pydantic import ValidationError

from src.core.config import Settings, settings
from src.integrations.calendar.client import CalendarApiClient
from src.integrations.calendar.exceptions import (
    CalendarApiBadRequest,
    CalendarApiForbidden,
    CalendarApiNotFound,
    CalendarApiProtocolError,
    CalendarApiUnauthorized,
    CalendarApiUnavailable,
)
from src.integrations.calendar.schemas import (
    BuscarProximaColetaToolResult,
    CalendarFilters,
    CalendarToolError,
    ListarAgendamentosToolResult,
)
from src.security.request_context import current_bearer_token
from src.shared.context import UserContext

mcp = MCPServer(
    "EcoCiente MCP",
    instructions=(
        "Expõe capacidades do EcoCiente para agentes. Identidade e Bearer token "
        "são fornecidos pela infraestrutura e nunca fazem parte dos argumentos das tools."
    ),
)


@dataclass(frozen=True, slots=True)
class CalendarToolRuntime:
    calendar_api: CalendarApiClient | None
    settings: Settings
    user: UserContext


_calendar_runtime: ContextVar[CalendarToolRuntime | None] = ContextVar("calendar_mcp_runtime", default=None)


@contextmanager
def calendar_tool_context(
    calendar_api: CalendarApiClient | None,
    app_settings: Settings,
    user: UserContext,
) -> Iterator[None]:
    """Injeta infraestrutura/identidade fora do schema visível ao modelo."""
    token = _calendar_runtime.set(CalendarToolRuntime(calendar_api=calendar_api, settings=app_settings, user=user))
    try:
        yield
    finally:
        _calendar_runtime.reset(token)


def _runtime() -> CalendarToolRuntime:
    runtime = _calendar_runtime.get()
    if runtime is None:
        raise RuntimeError("Contexto seguro do calendário não foi configurado para esta chamada MCP.")
    return runtime


def _bearer_token(runtime: CalendarToolRuntime) -> str | None:
    # Chamadas diretas de testes podem fornecer token no UserContext. No fluxo
    # FastAPI real, o token fica fora do estado LangGraph e é lido do contexto
    # assíncrono da request.
    if runtime.user.token.strip():
        return runtime.user.token.strip()
    request_token = current_bearer_token()
    if request_token:
        return request_token
    if runtime.settings.environment == "test" and runtime.settings.calendar_test_bearer_token:
        return runtime.settings.calendar_test_bearer_token
    return None


def _tool_error(exc: Exception) -> CalendarToolError:
    if isinstance(exc, CalendarApiUnauthorized):
        return CalendarToolError(code="unauthorized", message="A autenticação foi rejeitada pelo calendário.", http_status=401)
    if isinstance(exc, CalendarApiForbidden):
        return CalendarToolError(code="forbidden", message="Seu perfil não possui autorização para esta consulta.", http_status=403)
    if isinstance(exc, CalendarApiNotFound):
        return CalendarToolError(code="not_found", message="O recurso solicitado não foi encontrado no calendário.", http_status=404)
    if isinstance(exc, CalendarApiBadRequest):
        return CalendarToolError(code="bad_request", message=str(exc), http_status=400)
    if isinstance(exc, CalendarApiUnavailable):
        return CalendarToolError(code="unavailable", message="O serviço de calendário está indisponível.", http_status=503)
    if isinstance(exc, CalendarApiProtocolError):
        return CalendarToolError(code="protocol_error", message="O calendário respondeu fora do contrato esperado.")
    return CalendarToolError(code="internal_error", message="Falha interna ao executar a capacidade de calendário.")


def _build_filters(
    *,
    status: str | None = None,
    data_inicio: str | None = None,
    data_fim: str | None = None,
    possui_recorrencia: bool | None = None,
    condominio_id: int | None = None,
    cooperativa_id: int | None = None,
) -> CalendarFilters:
    return CalendarFilters(
        status=status,
        data_inicio=data_inicio,
        data_fim=data_fim,
        possui_recorrencia=possui_recorrencia,
        condominio_id=condominio_id,
        cooperativa_id=cooperativa_id,
    )


@mcp.tool(title="Consultar guia EcoCiente")
def consultar_guia_ecociente(pergunta: str, limite: int = 3) -> dict:
    """Consulta trechos da base oficial local do EcoCiente sem executar LLM."""
    import re

    limite = max(1, min(limite, 5))
    path = settings.knowledge_base_file
    text = path.read_text(encoding="utf-8")
    sections: list[tuple[str, str]] = []
    title = "FAQ EcoCiente"
    buffer: list[str] = []
    for line in text.splitlines():
        if line.startswith("#"):
            if buffer:
                sections.append((title, "\n".join(buffer).strip()))
            title = line.lstrip("#").strip() or title
            buffer = []
        else:
            buffer.append(line)
    if buffer:
        sections.append((title, "\n".join(buffer).strip()))

    terms = {t for t in re.findall(r"[\wÀ-ÿ]+", pergunta.lower()) if len(t) >= 4}
    ranked: list[tuple[int, str, str]] = []
    for section_title, body in sections:
        haystack = f"{section_title} {body}".lower()
        score = sum(haystack.count(term) for term in terms)
        if score:
            ranked.append((score, section_title, body))
    ranked.sort(key=lambda item: item[0], reverse=True)
    results = [
        {
            "title": section_title,
            "source": settings.knowledge_base_path,
            "excerpt": " ".join(body.split())[:900],
        }
        for _, section_title, body in ranked[:limite]
    ]
    return {"query": pergunta, "results": results, "evidence_found": bool(results)}


@mcp.tool(title="Listar agendamentos de coleta")
async def listar_agendamentos_coleta(
    status: str | None = None,
    data_inicio: str | None = None,
    data_fim: str | None = None,
    possui_recorrencia: bool | None = None,
    condominio_id: int | None = None,
    cooperativa_id: int | None = None,
    page: int = 0,
    size: int = 10,
    sort: str = "dataInicio,asc",
) -> ListarAgendamentosToolResult:
    """Lista agendamentos do usuário autenticado com filtros suportados pela API de calendário.

    A identidade, o perfil e o Bearer token não são argumentos desta tool: vêm
    exclusivamente do contexto autenticado da infraestrutura.
    """
    try:
        runtime = _runtime()
    except RuntimeError:
        return ListarAgendamentosToolResult(
            ok=False,
            error=CalendarToolError(code="authentication_required", message="Contexto autenticado do calendário ausente."),
        )
    if runtime.calendar_api is None:
        return ListarAgendamentosToolResult(
            ok=False,
            error=CalendarToolError(code="not_configured", message="Integração de calendário não configurada."),
        )
    token = _bearer_token(runtime)
    if token is None:
        return ListarAgendamentosToolResult(
            ok=False,
            error=CalendarToolError(code="authentication_required", message="Bearer token do calendário indisponível."),
        )
    try:
        filters = _build_filters(
            status=status,
            data_inicio=data_inicio,
            data_fim=data_fim,
            possui_recorrencia=possui_recorrencia,
            condominio_id=condominio_id,
            cooperativa_id=cooperativa_id,
        )
        result = await runtime.calendar_api.listar_agendamentos(
            token=token,
            filters=filters,
            page=page,
            size=size,
            sort=sort,
        )
        return ListarAgendamentosToolResult(
            ok=True,
            agendamentos=result.content,
            page=result.number,
            size=result.size,
            total_elements=result.total_elements,
            total_pages=result.total_pages,
            first=result.first,
            last=result.last,
        )
    except (ValidationError, ValueError) as exc:
        return ListarAgendamentosToolResult(
            ok=False,
            error=CalendarToolError(code="bad_request", message=f"Filtros inválidos: {exc}", http_status=400),
        )
    except (CalendarApiBadRequest, CalendarApiUnauthorized, CalendarApiForbidden, CalendarApiNotFound, CalendarApiUnavailable, CalendarApiProtocolError) as exc:
        return ListarAgendamentosToolResult(ok=False, error=_tool_error(exc))


@mcp.tool(title="Buscar próxima coleta")
async def buscar_proxima_coleta(
    data_inicio: str | None = None,
    data_fim: str | None = None,
    possui_recorrencia: bool | None = None,
    condominio_id: int | None = None,
    cooperativa_id: int | None = None,
) -> BuscarProximaColetaToolResult:
    """Busca a próxima coleta do usuário autenticado, respeitando o escopo aplicado pela API Java."""
    try:
        runtime = _runtime()
    except RuntimeError:
        return BuscarProximaColetaToolResult(
            ok=False,
            error=CalendarToolError(code="authentication_required", message="Contexto autenticado do calendário ausente."),
        )
    if runtime.calendar_api is None:
        return BuscarProximaColetaToolResult(
            ok=False,
            error=CalendarToolError(code="not_configured", message="Integração de calendário não configurada."),
        )
    token = _bearer_token(runtime)
    if token is None:
        return BuscarProximaColetaToolResult(
            ok=False,
            error=CalendarToolError(code="authentication_required", message="Bearer token do calendário indisponível."),
        )
    try:
        filters = _build_filters(
            data_inicio=data_inicio,
            data_fim=data_fim,
            possui_recorrencia=possui_recorrencia,
            condominio_id=condominio_id,
            cooperativa_id=cooperativa_id,
        )
        agendamento = await runtime.calendar_api.buscar_proxima_coleta(token=token, filters=filters)
        return BuscarProximaColetaToolResult(ok=True, found=agendamento is not None, agendamento=agendamento)
    except (ValidationError, ValueError) as exc:
        return BuscarProximaColetaToolResult(
            ok=False,
            error=CalendarToolError(code="bad_request", message=f"Filtros inválidos: {exc}", http_status=400),
        )
    except (CalendarApiBadRequest, CalendarApiUnauthorized, CalendarApiForbidden, CalendarApiNotFound, CalendarApiUnavailable, CalendarApiProtocolError) as exc:
        return BuscarProximaColetaToolResult(ok=False, error=_tool_error(exc))


if __name__ == "__main__":
    mcp.run()
