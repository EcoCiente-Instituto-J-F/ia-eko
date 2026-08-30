from __future__ import annotations

import asyncio
from datetime import date
import logging
from typing import Any

import httpx
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger("ecociente.integrations.calendar")


class CalendarApiError(RuntimeError):
    """Erro base da API externa de calendário."""


class CalendarApiUnavailable(CalendarApiError):
    """A API de calendário não respondeu com segurança."""


class CalendarApiProtocolError(CalendarApiError):
    """A resposta não respeita o contrato mínimo do calendário."""


class CalendarEvent(BaseModel):
    """Representação normalizada de um compromisso retornado pela API externa."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    event_id: str | None = Field(default=None, validation_alias=AliasChoices("id", "event_id"))
    condominio_id: int | None = Field(default=None, validation_alias=AliasChoices("condominio_id", "condominium_id"))
    cooperativa_id: int | None = Field(default=None, validation_alias=AliasChoices("cooperativa_id", "cooperative_id"))
    data: date
    horario: str
    cooperativa: str
    status: str


class CalendarApiClient:
    """Adaptador HTTP isolado para a API de agenda de coletas.

    O contrato assumido mantém o endpoint de consulta solicitado:
    ``GET /calendar/events/{condominio_id}``. Outros detalhes de fornecedor
    ficam encapsulados neste arquivo e não vazam para agentes.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 8.0,
        retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.retries = max(0, retries)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            transport=transport,
            follow_redirects=False,
        )

    async def events_for_condominium(self, condominio_id: int, *, token: str | None = None) -> list[CalendarEvent]:
        payload = await self._request("GET", f"/calendar/events/{condominio_id}", token=token)
        return self._events_from_payload(payload)

    async def events_for_cooperative(
        self,
        cooperativa_id: int,
        *,
        status: str | None = None,
        token: str | None = None,
    ) -> list[CalendarEvent]:
        params: dict[str, str] = {"cooperativa_id": str(cooperativa_id)}
        if status:
            params["status"] = status
        payload = await self._request("GET", "/calendar/events", params=params, token=token)
        return self._events_from_payload(payload)

    async def update_event(
        self,
        event_id: str,
        changes: dict[str, Any],
        *,
        token: str | None = None,
    ) -> CalendarEvent:
        payload = await self._request("PATCH", f"/calendar/events/{event_id}", payload=changes, token=token)
        if isinstance(payload, dict) and isinstance(payload.get("coleta"), dict):
            payload = payload["coleta"]
        if not isinstance(payload, dict):
            raise CalendarApiProtocolError("A atualização não retornou um evento válido.")
        try:
            return CalendarEvent.model_validate(payload)
        except ValidationError as exc:
            raise CalendarApiProtocolError("A atualização retornou um evento inválido.") from exc

    async def create_event(self, event: dict[str, Any], *, token: str | None = None) -> CalendarEvent:
        """Cria um compromisso na API externa; não mantém estado local."""
        payload = await self._request("POST", "/calendar/events", payload=event, token=token)
        if isinstance(payload, dict) and isinstance(payload.get("coleta"), dict):
            payload = payload["coleta"]
        if not isinstance(payload, dict):
            raise CalendarApiProtocolError("A criação não retornou um evento válido.")
        try:
            return CalendarEvent.model_validate(payload)
        except ValidationError as exc:
            raise CalendarApiProtocolError("A criação retornou um evento inválido.") from exc

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> Any:
        # GET é idempotente e pode usar retry. PATCH/POST não são repetidos para
        # evitar confirmar, reagendar ou criar duas vezes em falhas ambíguas.
        attempts = self.retries if method == "GET" else 0
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        for attempt in range(attempts + 1):
            try:
                response = await self._client.request(
                    method,
                    f"{self.base_url}{path}",
                    params=params,
                    json=payload,
                    headers=headers,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt < attempts:
                    await asyncio.sleep(0.1 * (attempt + 1))
                    continue
                logger.warning("calendar_api_unavailable", extra={"method": method, "attempts": attempt + 1})
                raise CalendarApiUnavailable("A API de calendário está indisponível.") from exc

            if response.status_code >= 500:
                if attempt < attempts:
                    await asyncio.sleep(0.1 * (attempt + 1))
                    continue
                logger.warning(
                    "calendar_api_server_error",
                    extra={"method": method, "status_code": response.status_code, "attempts": attempt + 1},
                )
                raise CalendarApiUnavailable("A API de calendário está indisponível.")
            if response.status_code >= 400:
                logger.warning("calendar_api_client_error", extra={"method": method, "status_code": response.status_code})
                raise CalendarApiProtocolError("A API de calendário recusou a solicitação.")

            try:
                return response.json()
            except ValueError as exc:
                raise CalendarApiProtocolError("A API de calendário não retornou JSON válido.") from exc

        raise CalendarApiUnavailable("A API de calendário está indisponível.")

    @staticmethod
    def _events_from_payload(payload: Any) -> list[CalendarEvent]:
        if isinstance(payload, dict):
            if isinstance(payload.get("coleta"), dict):
                raw_events = [payload["coleta"]]
            else:
                raw_events = payload.get("events", payload.get("coletas"))
        else:
            raw_events = payload
        if not isinstance(raw_events, list):
            raise CalendarApiProtocolError("A API de calendário não retornou uma lista de eventos.")
        try:
            return [CalendarEvent.model_validate(event) for event in raw_events]
        except ValidationError as exc:
            raise CalendarApiProtocolError("A API de calendário retornou evento inválido.") from exc

    async def close(self) -> None:
        await self._client.aclose()
