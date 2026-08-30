"""Integração tipada com a API REST de calendário de coletas."""

from src.integrations.calendar.client import CalendarApiClient
from src.integrations.calendar.exceptions import (
    CalendarApiBadRequest,
    CalendarApiError,
    CalendarApiForbidden,
    CalendarApiNotFound,
    CalendarApiProtocolError,
    CalendarApiUnauthorized,
    CalendarApiUnavailable,
)
from src.integrations.calendar.schemas import Agendamento, AgendamentoPage, CalendarFilters, CalendarStatus

__all__ = [
    "Agendamento",
    "AgendamentoPage",
    "CalendarApiBadRequest",
    "CalendarApiClient",
    "CalendarApiError",
    "CalendarApiForbidden",
    "CalendarApiNotFound",
    "CalendarApiProtocolError",
    "CalendarApiUnauthorized",
    "CalendarApiUnavailable",
    "CalendarFilters",
    "CalendarStatus",
]
