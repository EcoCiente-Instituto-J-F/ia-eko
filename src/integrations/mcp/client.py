from __future__ import annotations

import asyncio
from typing import Any, TypeVar

from mcp import Client
from pydantic import BaseModel, ValidationError

from src.core.config import Settings
from src.integrations.calendar.client import CalendarApiClient
from src.integrations.calendar.schemas import BuscarProximaColetaToolResult, ListarAgendamentosToolResult
from src.integrations.mcp.server import calendar_tool_context, mcp
from src.shared.context import UserContext

T = TypeVar("T", bound=BaseModel)


class McpToolProtocolError(RuntimeError):
    """O servidor MCP não devolveu o structured_content esperado."""


class CalendarMcpClient:
    """Cliente MCP usado pelo agente Coletas.

    A tool recebe apenas filtros semânticos. Token e identidade entram pelo
    contexto seguro antes da sessão MCP e nunca são enviados como argumentos.
    """

    def __init__(self, calendar_api: CalendarApiClient | None, settings: Settings):
        self.calendar_api = calendar_api
        self.settings = settings

    async def listar_agendamentos(self, user: UserContext, **arguments: Any) -> ListarAgendamentosToolResult:
        return await self._call(
            user,
            "listar_agendamentos_coleta",
            arguments,
            ListarAgendamentosToolResult,
        )

    async def buscar_proxima(self, user: UserContext, **arguments: Any) -> BuscarProximaColetaToolResult:
        return await self._call(
            user,
            "buscar_proxima_coleta",
            arguments,
            BuscarProximaColetaToolResult,
        )

    async def _call(self, user: UserContext, tool_name: str, arguments: dict[str, Any], model: type[T]) -> T:
        clean_arguments = {key: value for key, value in arguments.items() if value is not None}
        with calendar_tool_context(self.calendar_api, self.settings, user):
            async with Client(mcp) as client:
                result = await client.call_tool(tool_name, clean_arguments)
        if result.is_error or result.structured_content is None:
            raise McpToolProtocolError(f"Falha MCP ao executar {tool_name}.")
        try:
            return model.model_validate(result.structured_content)
        except ValidationError as exc:
            raise McpToolProtocolError(f"Resposta estruturada inválida da tool {tool_name}.") from exc


async def consultar_via_mcp(pergunta: str) -> object:
    """Cliente de demonstração para a tool local de conhecimento."""
    async with Client(mcp) as client:
        return await client.call_tool("consultar_guia_ecociente", {"pergunta": pergunta, "limite": 3})


async def _main() -> None:
    result = await consultar_via_mcp("Como separar resíduos recicláveis?")
    print(result)


if __name__ == "__main__":
    asyncio.run(_main())
