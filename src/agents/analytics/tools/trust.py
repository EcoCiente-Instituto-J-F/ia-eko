from __future__ import annotations

from typing import Any

import psycopg2
from langchain.tools import tool
from pydantic import BaseModel, Field

from src.agents.analytics.tools.common import db_failure
from src.database.postgres import PostgresUnavailable, postgres_db


class ResumoConfiancaArgs(BaseModel):
    usuario_id: int = Field(gt=0)
    condominio_id: int | None = Field(default=None, gt=0)


@tool("resumo_confianca_usuario", args_schema=ResumoConfiancaArgs)
def resumo_confianca_usuario(usuario_id: int, condominio_id: int | None = None) -> dict[str, Any]:
    """Retorna o trust_score do próprio usuário e seus indicadores de moderação."""
    try:
        params: list[Any] = [usuario_id]
        sql = """SELECT r.condominio_id,r.trust_score,r.postagens_validadas_sem_contestacao,
                        r.denuncias_realizadas,r.denuncias_procedentes,r.taxa_acerto_denuncias,
                        n.nome_nivel,n.peso_voto
                   FROM tb_rel_usuarios_condominios r
                   JOIN tb_lkp_niveis_confianca n ON n.id_nivel_confianca=r.nivel_confianca_id
                  WHERE r.usuario_id=%s AND r.aprovado=TRUE AND r.data_saida IS NULL"""
        if condominio_id:
            sql += " AND r.condominio_id=%s"; params.append(condominio_id)
        sql += " ORDER BY r.data_entrada DESC LIMIT 1"
        return {"status": "ok", "usuario_id": usuario_id, "confianca": postgres_db.fetch_one(sql, params)}
    except (PostgresUnavailable, psycopg2.Error) as exc:
        return db_failure(exc)


TOOLS = [resumo_confianca_usuario]
