from __future__ import annotations


class CalendarApiError(RuntimeError):
    """Erro base da integração com a API REST de calendário."""


class CalendarApiUnavailable(CalendarApiError):
    """Timeout, falha de transporte ou erro 5xx da API de calendário."""


class CalendarApiBadRequest(CalendarApiError):
    """A API rejeitou filtros/parâmetros da consulta (HTTP 400)."""


class CalendarApiUnauthorized(CalendarApiError):
    """O Bearer token foi rejeitado pela API de calendário (HTTP 401)."""


class CalendarApiForbidden(CalendarApiError):
    """O usuário autenticado não possui autorização na API (HTTP 403)."""


class CalendarApiNotFound(CalendarApiError):
    """Recurso não encontrado (HTTP 404)."""


class CalendarApiProtocolError(CalendarApiError):
    """Resposta da API incompatível com o contrato implementado em Java."""
