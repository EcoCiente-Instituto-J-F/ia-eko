from __future__ import annotations

from typing import Any

import psycopg2
from langchain.tools import tool
from pydantic import BaseModel, Field

from src.agents.analytics.tools.common import date_filters, db_failure, resolve_period
from src.database.postgres import PostgresUnavailable, postgres_db


class DesempenhoQuizzesArgs(BaseModel):
    condominio_id: int = Field(gt=0)
    data_inicio: str | None = None
    data_fim: str | None = None
    periodo: str | None = None


@tool("desempenho_quizzes_condominio", args_schema=DesempenhoQuizzesArgs)
def desempenho_quizzes_condominio(condominio_id: int, data_inicio: str | None = None, data_fim: str | None = None, periodo: str | None = None) -> dict[str, Any]:
    """Métricas agregadas de tentativas de quiz de um condomínio."""
    try:
        data_inicio, data_fim = resolve_period(periodo, data_inicio, data_fim)
        params: list[Any] = [condominio_id]
        join_filter = date_filters("t.iniciado_em", data_inicio, data_fim, params)
        rows = postgres_db.fetch_all(
            """SELECT q.id_quiz,q.titulo_quiz,COUNT(t.id_tentativa)::bigint AS total_tentativas,
                      COUNT(t.id_tentativa) FILTER(WHERE t.aprovado=TRUE)::bigint AS aprovadas,
                      ROUND(AVG(t.nota) FILTER(WHERE t.concluido_em IS NOT NULL),2) AS nota_media,
                      ROUND(100.0*COUNT(t.id_tentativa) FILTER(WHERE t.aprovado=TRUE)/NULLIF(COUNT(t.id_tentativa) FILTER(WHERE t.concluido_em IS NOT NULL),0),2) AS taxa_aprovacao_percentual
                 FROM tb_quizzes q
                 LEFT JOIN tb_tentativas_quiz t ON t.quiz_id=q.id_quiz AND t.condominio_id=%s""" + join_filter + """
                WHERE q.ativo=TRUE
                GROUP BY q.id_quiz,q.titulo_quiz ORDER BY total_tentativas DESC,q.titulo_quiz""",
            params,
        )
        return {"status": "ok", "condominio_id": condominio_id, "periodo": {"data_inicio": data_inicio, "data_fim": data_fim}, "quizzes": rows}
    except (PostgresUnavailable, psycopg2.Error) as exc:
        return db_failure(exc)


TOOLS = [desempenho_quizzes_condominio]
