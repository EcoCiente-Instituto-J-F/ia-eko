"""Capacidades MCP usadas pelo agente de Coletas."""

from __future__ import annotations

from typing import Any

from src.integrations.calendar.schemas import BuscarProximaColetaToolResult, ListarAgendamentosToolResult
from src.integrations.mcp.client import CalendarMcpClient
from src.shared.context import UserContext


class CalendarTools:
    """Ponte do agente para MCP; não conhece HTTP, URL ou Authorization header."""

    def __init__(self, mcp_client: CalendarMcpClient):
        self.mcp_client = mcp_client

    async def next_collection(self, user: UserContext, **filters: Any) -> BuscarProximaColetaToolResult:
        return await self.mcp_client.buscar_proxima(user, **filters)

    async def list_collections(self, user: UserContext, **filters: Any) -> ListarAgendamentosToolResult:
        return await self.mcp_client.listar_agendamentos(user, **filters)
