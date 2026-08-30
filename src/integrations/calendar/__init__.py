"""Integração com a API externa de calendário de coletas."""

from src.integrations.calendar.client import (
    CalendarApiClient,
    CalendarApiError,
    CalendarApiProtocolError,
    CalendarApiUnavailable,
    CalendarEvent,
)

__all__ = [
    "CalendarApiClient",
    "CalendarApiError",
    "CalendarApiProtocolError",
    "CalendarApiUnavailable",
    "CalendarEvent",
]
