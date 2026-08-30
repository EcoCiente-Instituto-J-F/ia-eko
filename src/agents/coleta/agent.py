from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import re
import unicodedata

from src.agents.coleta.tools import CalendarTools
from src.integrations.calendar.schemas import Agendamento, CalendarToolError
from src.integrations.mcp.client import CalendarMcpClient, McpToolProtocolError
from src.security.policies import action_for_route, authorize_action
from src.shared.context import UserContext

AGENT_NAME = "coletas"


@dataclass(frozen=True, slots=True)
class CollectionAgentResult:
    answer: str
    action: str


class CollectionAgent:
    """Especialista de Coletas orientado a capacidades MCP reais.

    A API Java anexada implementa somente consultas GET. Solicitações de criar,
    confirmar ou alterar agendamentos são respondidas como indisponíveis nesta
    versão, sem fabricar chamadas REST.
    """

    def __init__(self, mcp_client: CalendarMcpClient):
        self.tools = CalendarTools(mcp_client)

    async def run(self, message: str, user: UserContext) -> CollectionAgentResult:
        action = action_for_route("coletas", message)
        decision = authorize_action(user, action)
        if not decision.allowed:
            return CollectionAgentResult(decision.message, action)

        if action in {"coleta_confirmar", "coleta_gerenciar"}:
            return CollectionAgentResult(
                "A API de calendário disponível nesta versão permite apenas consultar agendamentos e a próxima coleta; "
                "criação, confirmação e alteração ainda não possuem endpoint implementado.",
                action,
            )

        try:
            if self._should_list(message, action):
                return await self._list(message, user, action)
            return await self._next(message, user, action)
        except McpToolProtocolError:
            return CollectionAgentResult(
                "Não consegui executar a consulta ao calendário pelo MCP neste momento.",
                action,
            )

    async def _next(self, message: str, user: UserContext, action: str) -> CollectionAgentResult:
        filters = self._filters_from_message(message, include_status=False)
        result = await self.tools.next_collection(user, **filters)
        if not result.ok:
            return CollectionAgentResult(self._error_message(result.error), action)
        if not result.found or result.agendamento is None:
            return CollectionAgentResult("Não há próxima coleta encontrada para os filtros informados.", action)
        return CollectionAgentResult(f"Sua próxima coleta é {self._format_agendamento(result.agendamento)}.", action)

    async def _list(self, message: str, user: UserContext, action: str) -> CollectionAgentResult:
        filters = self._filters_from_message(message, include_status=True)
        result = await self.tools.list_collections(user, **filters)
        if not result.ok:
            return CollectionAgentResult(self._error_message(result.error), action)
        if not result.agendamentos:
            return CollectionAgentResult("Não encontrei agendamentos para os filtros informados.", action)
        items = "; ".join(self._format_agendamento(item) for item in result.agendamentos[:5])
        suffix = ""
        if result.total_elements > len(result.agendamentos):
            suffix = f" Página {result.page + 1} de {max(result.total_pages, 1)}; {result.total_elements} agendamentos no total."
        return CollectionAgentResult(f"Agendamentos encontrados: {items}.{suffix}", action)

    @classmethod
    def _should_list(cls, message: str, action: str) -> bool:
        if action == "coleta_agenda":
            return True
        text = cls._normalize(message)
        markers = (
            "liste",
            "listar",
            "quais",
            "agenda",
            "tem coleta",
            "ha coleta",
            "confirmad",
            "agendad",
            "cancelad",
            "realizad",
            "recusad",
            "recorrente",
            "entre segunda e sexta",
        )
        return any(marker in text for marker in markers)

    @classmethod
    def _filters_from_message(cls, message: str, *, include_status: bool) -> dict[str, object]:
        text = cls._normalize(message)
        filters: dict[str, object] = {}

        if include_status:
            status_markers = {
                "confirmad": "CONFIRMADO",
                "agendad": "AGENDADO",
                "recusad": "RECUSADO",
                "cancelad": "CANCELADO",
                "realizad": "REALIZADO",
            }
            for marker, value in status_markers.items():
                if marker in text:
                    filters["status"] = value
                    break

        if "nao recorrente" in text or "sem recorrencia" in text:
            filters["possui_recorrencia"] = False
        elif "recorrente" in text or "recorrencia" in text:
            filters["possui_recorrencia"] = True

        cooperativa_id = cls._id_from_message(message, "cooperativa")
        if cooperativa_id is not None:
            filters["cooperativa_id"] = cooperativa_id
        condominio_id = cls._id_from_message(message, "condomínio|condominio")
        if condominio_id is not None:
            filters["condominio_id"] = condominio_id

        period = cls._period_from_message(message)
        if period is not None:
            filters["data_inicio"] = period[0].isoformat()
            filters["data_fim"] = period[1].isoformat()
        return filters

    @classmethod
    def _period_from_message(cls, message: str) -> tuple[datetime, datetime] | None:
        text = cls._normalize(message)
        today = date.today()
        if "amanha" in text:
            target = today + timedelta(days=1)
            return datetime.combine(target, time.min), datetime.combine(target, time.max)
        if "hoje" in text:
            return datetime.combine(today, time.min), datetime.combine(today, time.max)
        if "entre segunda e sexta" in text or "de segunda a sexta" in text:
            monday = today - timedelta(days=today.weekday())
            friday = monday + timedelta(days=4)
            if friday < today:
                monday += timedelta(days=7)
                friday += timedelta(days=7)
            start_day = max(today, monday)
            return datetime.combine(start_day, time.min), datetime.combine(friday, time.max)

        parsed = cls._message_date(message)
        if parsed is not None:
            return datetime.combine(parsed, time.min), datetime.combine(parsed, time.max)
        return None

    @staticmethod
    def _id_from_message(message: str, label_pattern: str) -> int | None:
        match = re.search(rf"(?:{label_pattern})\s*#?(\d+)", message, re.IGNORECASE)
        return int(match.group(1)) if match else None

    @staticmethod
    def _message_date(message: str) -> date | None:
        match = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", message)
        if match:
            try:
                return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
            except ValueError:
                return None
        match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", message)
        if match:
            try:
                return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except ValueError:
                return None
        return None

    @staticmethod
    def _format_agendamento(item: Agendamento) -> str:
        end = f" até {item.data_fim.strftime('%H:%M')}" if item.data_fim else ""
        recurrence = "recorrente" if item.possui_recorrencia else "avulsa"
        return (
            f"#{item.id} em {item.data_inicio.strftime('%d/%m/%Y às %H:%M')}{end}, "
            f"condomínio {item.condominio_id}, cooperativa {item.cooperativa_id}, "
            f"status {item.status_agendamento.value}, {recurrence}"
        )

    @staticmethod
    def _error_message(error: CalendarToolError | None) -> str:
        if error is None:
            return "Não foi possível interpretar a resposta do calendário."
        messages = {
            "authentication_required": "Não há credencial autenticada disponível para consultar o calendário.",
            "unauthorized": "Sua sessão não foi aceita pela API de calendário; autentique-se novamente.",
            "forbidden": "Seu perfil não possui autorização para essa consulta no calendário.",
            "bad_request": "Os filtros enviados ao calendário não foram aceitos.",
            "unavailable": "O serviço de calendário está indisponível neste momento.",
            "not_configured": "A integração com a API de calendário ainda não está configurada neste ambiente.",
            "not_found": "O recurso solicitado não foi encontrado no calendário.",
            "protocol_error": "A API de calendário respondeu fora do contrato esperado.",
        }
        return messages.get(error.code, "Não consegui consultar o calendário neste momento.")

    @staticmethod
    def _normalize(value: str) -> str:
        value = unicodedata.normalize("NFD", value.lower())
        value = "".join(char for char in value if unicodedata.category(char) != "Mn")
        return " ".join(value.split())
