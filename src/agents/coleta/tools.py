"""Ferramentas específicas do agente de Coletas."""

from src.integrations.calendar.client import CalendarEvent
from src.services.calendar_service import CalendarService
from src.shared.context import UserContext


class CalendarTools:
    """Ponte tipada entre o agente e o serviço de calendário externo."""

    def __init__(self, calendar: CalendarService):
        self.calendar = calendar

    async def next_collection(self, user: UserContext) -> CalendarEvent | None:
        if user.condominio_id is None:
            return None
        return await self.calendar.next_collection(user.condominio_id, token=user.token)

    async def cooperative_schedule(self, user: UserContext, *, status: str | None = None) -> list[CalendarEvent]:
        if user.cooperativa_id is None:
            return []
        return await self.calendar.cooperative_schedule(user.cooperativa_id, status=status, token=user.token)

    async def confirm(self, user: UserContext, event_id: str) -> CalendarEvent:
        return await self.calendar.confirm_collection(event_id, token=user.token)

    async def reschedule(self, user: UserContext, event_id: str, data: str, horario: str | None = None) -> CalendarEvent:
        return await self.calendar.reschedule_collection(event_id, data=data, horario=horario, token=user.token)

    async def schedule(
        self,
        user: UserContext,
        *,
        cooperativa_id: int,
        data: str | None,
        dia_semana: str | None,
        recorrente: bool,
    ) -> CalendarEvent:
        if user.condominio_id is None:
            raise ValueError("condominio_id ausente no contexto autenticado")
        return await self.calendar.schedule_collection(
            condominio_id=user.condominio_id,
            cooperativa_id=cooperativa_id,
            data=data,
            dia_semana=dia_semana,
            recorrente=recorrente,
            token=user.token,
        )
