from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx
from pydantic import ValidationError

from src.integrations.calendar.exceptions import (
    CalendarApiBadRequest,
    CalendarApiForbidden,
    CalendarApiNotFound,
    CalendarApiProtocolError,
    CalendarApiUnauthorized,
    CalendarApiUnavailable,
)
from src.integrations.calendar.schemas import Agendamento, AgendamentoPage, CalendarApiErrorResponse, CalendarFilters

logger = logging.getLogger("ecociente.integrations.calendar")
_SORT_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:,(?:asc|desc))?$", re.IGNORECASE)


class CalendarApiClient:
    """Cliente HTTP assíncrono para os endpoints realmente implementados pela API Java.

    Endpoints suportados:
    - GET /api/v1/agendamentos
    - GET /api/v1/agendamentos/proxima

    O Bearer token é recebido pela camada de infraestrutura e nunca é colocado
    em prompts, argumentos MCP, respostas ou logs.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.retries = max(0, retries)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            transport=transport,
            follow_redirects=False,
            headers={"Accept": "application/json"},
        )

    async def listar_agendamentos(
        self,
        *,
        token: str,
        filters: CalendarFilters | None = None,
        page: int = 0,
        size: int = 10,
        sort: str = "dataInicio,asc",
    ) -> AgendamentoPage:
        if page < 0:
            raise ValueError("page deve ser >= 0")
        if size < 1 or size > 100:
            raise ValueError("size deve estar entre 1 e 100")
        if not _SORT_PATTERN.fullmatch(sort.strip()):
            raise ValueError("sort deve usar o formato campo[,asc|desc]")
        params = (filters or CalendarFilters()).to_query_params()
        params.update({"page": str(page), "size": str(size), "sort": sort})
        payload = await self._request("GET", "/api/v1/agendamentos", token=token, params=params)
        try:
            return AgendamentoPage.model_validate(payload)
        except ValidationError as exc:
            raise CalendarApiProtocolError("A API de calendário retornou uma página inválida.") from exc

    async def buscar_proxima_coleta(
        self,
        *,
        token: str,
        filters: CalendarFilters | None = None,
    ) -> Agendamento | None:
        params = (filters or CalendarFilters()).to_query_params(include_status=False)
        try:
            payload = await self._request("GET", "/api/v1/agendamentos/proxima", token=token, params=params)
        except CalendarApiNotFound:
            return None
        try:
            return Agendamento.model_validate(payload)
        except ValidationError as exc:
            raise CalendarApiProtocolError("A API de calendário retornou um agendamento inválido.") from exc

    async def health(self) -> str:
        """Verifica a API por endpoint OpenAPI público, sem token."""
        try:
            response = await self._client.get(f"{self.base_url}/v3/api-docs")
        except (httpx.TimeoutException, httpx.TransportError):
            return "unavailable"
        return "ok" if response.is_success else "unavailable"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        params: dict[str, str] | None = None,
    ) -> Any:
        if not token or not token.strip():
            raise CalendarApiUnauthorized("Bearer token ausente para a API de calendário.")

        headers = {"Authorization": f"Bearer {token.strip()}"}
        for attempt in range(self.retries + 1):
            try:
                response = await self._client.request(
                    method,
                    f"{self.base_url}{path}",
                    params=params,
                    headers=headers,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt < self.retries:
                    await asyncio.sleep(0.1 * (attempt + 1))
                    continue
                logger.warning("calendar_api_unavailable", extra={"method": method, "attempts": attempt + 1})
                raise CalendarApiUnavailable("A API de calendário está indisponível.") from exc

            if response.status_code >= 500:
                if attempt < self.retries:
                    await asyncio.sleep(0.1 * (attempt + 1))
                    continue
                logger.warning(
                    "calendar_api_server_error",
                    extra={"method": method, "status_code": response.status_code, "attempts": attempt + 1},
                )
                raise CalendarApiUnavailable("A API de calendário está indisponível.")

            if response.status_code == 400:
                raise CalendarApiBadRequest(self._safe_error_message(response, "A API rejeitou os filtros informados."))
            if response.status_code == 401:
                raise CalendarApiUnauthorized("A API de calendário rejeitou a autenticação.")
            if response.status_code == 403:
                raise CalendarApiForbidden("O usuário não possui autorização na API de calendário.")
            if response.status_code == 404:
                raise CalendarApiNotFound("Nenhum recurso de calendário foi encontrado.")
            if response.status_code >= 400:
                logger.warning("calendar_api_unexpected_status", extra={"method": method, "status_code": response.status_code})
                raise CalendarApiProtocolError(f"Resposta HTTP inesperada da API de calendário: {response.status_code}.")

            try:
                return response.json()
            except ValueError as exc:
                raise CalendarApiProtocolError("A API de calendário não retornou JSON válido.") from exc

        raise CalendarApiUnavailable("A API de calendário está indisponível.")

    @staticmethod
    def _safe_error_message(response: httpx.Response, fallback: str) -> str:
        try:
            parsed = CalendarApiErrorResponse.model_validate(response.json())
        except (ValueError, ValidationError):
            return fallback
        if not parsed.details:
            return fallback
        detail = parsed.details[0]
        return f"{parsed.codigo_error}: {detail.field} - {detail.message}"

    async def close(self) -> None:
        await self._client.aclose()
