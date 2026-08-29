from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import re
import unicodedata

from src.agents.coleta.tools import CalendarTools
from src.integrations.calendar.client import CalendarApiError, CalendarEvent
from src.security.policies import action_for_route, authorize_action
from src.services.calendar_service import CalendarService
from src.shared.context import UserContext

AGENT_NAME = "coletas"


@dataclass(frozen=True, slots=True)
class CollectionAgentResult:
    answer: str
    action: str


class CollectionAgent:
    """Especialista determinístico para operações apoiadas pela API externa.

    A decisão de acesso é repetida aqui como defesa em profundidade; a API
    externa continua sendo a única fonte de agenda e status.
    """

    def __init__(self, calendar: CalendarService):
        self.tools = CalendarTools(calendar)

    async def run(self, message: str, user: UserContext) -> CollectionAgentResult:
        action = action_for_route("coletas", message)
        decision = authorize_action(user, action)
        if not decision.allowed:
            return CollectionAgentResult(decision.message, action)

        try:
            if action == "coleta_confirmar":
                return await self._confirm(message, user)
            if action == "coleta_agenda":
                return await self._agenda(user)
            if action == "coleta_gerenciar":
                return await self._manage(message, user)
            return await self._next_collection(user)
        except CalendarApiError:
            if action == "coleta_consultar" or action == "coleta_agenda":
                return CollectionAgentResult(
                    "Não consegui consultar o calendário neste momento. Tente novamente mais tarde.",
                    action,
                )
            return CollectionAgentResult(
                "Não foi possível confirmar o resultado da atualização no calendário. Consulte a agenda antes de tentar novamente.",
                action,
            )

    async def _next_collection(self, user: UserContext) -> CollectionAgentResult:
        if user.condominio_id is None:
            return CollectionAgentResult(
                "Não encontrei um condomínio vinculado à sua conta para consultar a próxima coleta.",
                "coleta_consultar",
            )
        event = await self.tools.next_collection(user)
        if event is None:
            return CollectionAgentResult(
                "Não encontrei nenhuma coleta cadastrada para esse condomínio no calendário.",
                "coleta_consultar",
            )
        return CollectionAgentResult(
            f"A próxima coleta está {event.status} para {self._format_event(event)}.",
            "coleta_consultar",
        )

    async def _agenda(self, user: UserContext) -> CollectionAgentResult:
        if user.cooperativa_id is None:
            return CollectionAgentResult(
                "Não consegui identificar a cooperativa vinculada à sua conta para consultar a agenda.",
                "coleta_agenda",
            )
        events = await self.tools.cooperative_schedule(user)
        future = [event for event in events if event.data >= date.today()]
        if not future:
            return CollectionAgentResult("Não há compromissos futuros cadastrados para a cooperativa.", "coleta_agenda")
        summary = "; ".join(self._format_event(event) for event in sorted(future, key=self._event_sort_key)[:5])
        return CollectionAgentResult(f"Próximos compromissos da cooperativa: {summary}.", "coleta_agenda")

    async def _confirm(self, message: str, user: UserContext) -> CollectionAgentResult:
        if user.cooperativa_id is None:
            return CollectionAgentResult(
                "Não consegui identificar a cooperativa vinculada à sua conta para confirmar a passagem.",
                "coleta_confirmar",
            )
        events = await self.tools.cooperative_schedule(user)
        event = self._find_target_event(events, message)
        if event is None:
            return CollectionAgentResult(
                "Para confirmar a passagem, informe a data do compromisso ou o identificador do evento no calendário.",
                "coleta_confirmar",
            )
        if not event.event_id:
            return CollectionAgentResult(
                "O calendário não retornou o identificador do compromisso; não consigo confirmar a passagem com segurança.",
                "coleta_confirmar",
            )
        updated = await self.tools.confirm(user, event.event_id)
        return CollectionAgentResult(
            f"Passagem confirmada para {self._format_event(updated)}.",
            "coleta_confirmar",
        )

    async def _manage(self, message: str, user: UserContext) -> CollectionAgentResult:
        text = self._normalize(message)
        if any(marker in text for marker in ("remarcar", "alterar", "mudar")):
            return await self._reschedule(message, user)
        return await self._schedule(message, user)

    async def _reschedule(self, message: str, user: UserContext) -> CollectionAgentResult:
        event_id = self._event_id_from_message(message)
        data = self._date_from_message(message)
        if not event_id or not data:
            return CollectionAgentResult(
                "Para alterar uma coleta, informe o identificador do evento e a nova data no formato DD/MM/AAAA.",
                "coleta_gerenciar",
            )
        updated = await self.tools.reschedule(user, event_id, data)
        return CollectionAgentResult(f"Coleta atualizada para {self._format_event(updated)}.", "coleta_gerenciar")

    async def _schedule(self, message: str, user: UserContext) -> CollectionAgentResult:
        cooperativa_id = self._cooperative_id_from_message(message)
        recorrente = "recorrente" in self._normalize(message)
        data = self._date_from_message(message)
        dia_semana = self._weekday_from_message(message)
        if cooperativa_id is None:
            return CollectionAgentResult(
                "Para agendar a coleta, informe o identificador da cooperativa no formato “cooperativa 123”.",
                "coleta_gerenciar",
            )
        if recorrente and dia_semana is None:
            return CollectionAgentResult(
                "Qual dia da semana você deseja usar no agendamento recorrente?",
                "coleta_gerenciar",
            )
        if not recorrente and data is None:
            return CollectionAgentResult(
                "Qual data deseja usar no agendamento avulso? Informe no formato DD/MM/AAAA.",
                "coleta_gerenciar",
            )
        event = await self.tools.schedule(
            user,
            cooperativa_id=cooperativa_id,
            data=data,
            dia_semana=dia_semana,
            recorrente=recorrente,
        )
        kind = "recorrente" if recorrente else "avulsa"
        return CollectionAgentResult(f"Coleta {kind} criada para {self._format_event(event)}.", "coleta_gerenciar")

    @staticmethod
    def _normalize(value: str) -> str:
        value = unicodedata.normalize("NFD", value.lower())
        value = "".join(char for char in value if unicodedata.category(char) != "Mn")
        return " ".join(value.split())

    @staticmethod
    def _event_sort_key(event: CalendarEvent) -> tuple[date, str]:
        return event.data, event.horario

    @staticmethod
    def _format_event(event: CalendarEvent) -> str:
        return f"{event.data.strftime('%d/%m/%Y')} às {event.horario}, com {event.cooperativa} ({event.status})"

    @classmethod
    def _find_target_event(cls, events: list[CalendarEvent], message: str) -> CalendarEvent | None:
        event_id = cls._event_id_from_message(message)
        if event_id:
            return next((event for event in events if event.event_id == event_id), None)
        text = cls._normalize(message)
        target_date = date.today() + timedelta(days=1) if "amanha" in text else cls._message_date(message)
        if target_date:
            matches = [event for event in events if event.data == target_date]
            return matches[0] if len(matches) == 1 else None
        future = [event for event in events if event.data >= date.today()]
        return future[0] if len(future) == 1 else None

    @staticmethod
    def _event_id_from_message(message: str) -> str | None:
        match = re.search(r"(?:evento|compromisso)\s*(?:n[ºo]\.?\s*)?#?([A-Za-z0-9-]+)", message, re.IGNORECASE)
        return match.group(1) if match else None

    @staticmethod
    def _cooperative_id_from_message(message: str) -> int | None:
        match = re.search(r"cooperativa\s*#?(\d+)", message, re.IGNORECASE)
        return int(match.group(1)) if match else None

    @classmethod
    def _date_from_message(cls, message: str) -> str | None:
        parsed = cls._message_date(message)
        return parsed.isoformat() if parsed else None

    @staticmethod
    def _message_date(message: str) -> date | None:
        match = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", message)
        if not match:
            match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", message)
            if match:
                try:
                    return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
                except ValueError:
                    return None
            return None
        try:
            return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        except ValueError:
            return None

    @classmethod
    def _weekday_from_message(cls, message: str) -> str | None:
        text = cls._normalize(message)
        weekdays = {
            "segunda": "segunda-feira",
            "terca": "terça-feira",
            "quarta": "quarta-feira",
            "quinta": "quinta-feira",
            "sexta": "sexta-feira",
            "sabado": "sábado",
            "domingo": "domingo",
        }
        return next((value for key, value in weekdays.items() if key in text), None)
