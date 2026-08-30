from __future__ import annotations

from datetime import date

from src.integrations.calendar.client import CalendarApiClient, CalendarEvent


class CalendarService:
    """Regras de consulta delegadas à API externa, sem calendário interno."""

    def __init__(self, client: CalendarApiClient | None):
        self.client = client

    async def next_collection(self, condominio_id: int, *, token: str | None = None) -> CalendarEvent | None:
        events = await self.events_for_condominium(condominio_id, token=token)
        today = date.today()
        future = [event for event in events if event.data >= today]
        candidates = future or events
        return min(candidates, key=lambda event: (event.data, event.horario)) if candidates else None

    async def events_for_condominium(self, condominio_id: int, *, token: str | None = None) -> list[CalendarEvent]:
        return await self._required_client().events_for_condominium(condominio_id, token=token)

    async def cooperative_schedule(
        self,
        cooperativa_id: int,
        *,
        status: str | None = None,
        token: str | None = None,
    ) -> list[CalendarEvent]:
        return await self._required_client().events_for_cooperative(cooperativa_id, status=status, token=token)

    async def confirm_collection(self, event_id: str, *, token: str | None = None) -> CalendarEvent:
        return await self._required_client().update_event(event_id, {"status": "confirmada"}, token=token)

    async def reschedule_collection(
        self,
        event_id: str,
        *,
        data: str,
        horario: str | None = None,
        token: str | None = None,
    ) -> CalendarEvent:
        changes: dict[str, str] = {"data": data}
        if horario:
            changes["horario"] = horario
        return await self._required_client().update_event(event_id, changes, token=token)

    async def schedule_collection(
        self,
        *,
        condominio_id: int,
        cooperativa_id: int,
        data: str | None = None,
        dia_semana: str | None = None,
        recorrente: bool = False,
        token: str | None = None,
    ) -> CalendarEvent:
        payload: dict[str, object] = {
            "condominio_id": condominio_id,
            "cooperativa_id": cooperativa_id,
            "recorrente": recorrente,
        }
        if data:
            payload["data"] = data
        if dia_semana:
            payload["dia_semana"] = dia_semana
        return await self._required_client().create_event(payload, token=token)

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()

    def _required_client(self) -> CalendarApiClient:
        if self.client is None:
            from src.integrations.calendar.client import CalendarApiUnavailable

            raise CalendarApiUnavailable("Integração de calendário não configurada.")
        return self.client
