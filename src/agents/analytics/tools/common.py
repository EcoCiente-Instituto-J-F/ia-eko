from __future__ import annotations

import logging
import unicodedata
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg2

from src.database.postgres import PostgresUnavailable

logger = logging.getLogger("ecociente.analytics")
SP_TZ = ZoneInfo("America/Sao_Paulo")


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFD", value.lower())
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return " ".join(value.split())


def resolve_period(periodo: str | None, data_inicio: str | None, data_fim: str | None) -> tuple[str | None, str | None]:
    if data_inicio or data_fim:
        return data_inicio, data_fim
    if not periodo:
        return None, None
    today = datetime.now(SP_TZ).date()
    key = normalize_text(periodo).replace(" ", "_")
    if key in {"hoje", "dia"}:
        return today.isoformat(), today.isoformat()
    if key in {"semana", "ultima_semana", "7_dias", "ultimos_7_dias"}:
        return (today - timedelta(days=6)).isoformat(), today.isoformat()
    if key in {"mes", "mes_atual", "ciclo_atual"}:
        return today.replace(day=1).isoformat(), today.isoformat()
    if key == "mes_anterior":
        current_first = today.replace(day=1)
        previous_last = current_first - timedelta(days=1)
        return previous_last.replace(day=1).isoformat(), previous_last.isoformat()
    if key in {"ano", "ano_atual"}:
        return today.replace(month=1, day=1).isoformat(), today.isoformat()
    return None, None


def date_filters(column: str, data_inicio: str | None, data_fim: str | None, params: list[Any]) -> str:
    sql = ""
    if data_inicio:
        sql += f" AND ({column} AT TIME ZONE 'America/Sao_Paulo')::date >= %s::date"
        params.append(data_inicio)
    if data_fim:
        sql += f" AND ({column} AT TIME ZONE 'America/Sao_Paulo')::date <= %s::date"
        params.append(data_fim)
    return sql


def db_failure(exc: Exception) -> dict[str, str]:
    logger.exception("Falha de consulta Analytics", exc_info=exc)
    if isinstance(exc, PostgresUnavailable):
        return {"status": "unavailable", "message": str(exc)}
    if isinstance(exc, psycopg2.Error):
        return {"status": "error", "message": "A consulta PostgreSQL falhou; verifique schema e conectividade."}
    raise exc
