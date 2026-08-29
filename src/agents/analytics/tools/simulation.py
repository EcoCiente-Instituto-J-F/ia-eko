from __future__ import annotations

from typing import Any

import psycopg2
from langchain.tools import tool
from pydantic import BaseModel, Field

from src.agents.analytics.tools.common import db_failure
from src.database.postgres import PostgresUnavailable, postgres_db


def _ritmo_rows(condominio_id: int, dias_baseline: int) -> list[dict[str, Any]]:
    return postgres_db.fetch_all(
        """SELECT t.id_torre,t.nome_torre,
                  COUNT(p.id_postagem) FILTER (WHERE s.nome_status='aprovada')::bigint AS postagens_aprovadas,
                  ROUND(COUNT(p.id_postagem) FILTER (WHERE s.nome_status='aprovada')::numeric / %s, 4) AS postagens_por_dia
             FROM tb_torres t
             LEFT JOIN tb_postagens p ON p.torre_id=t.id_torre
                AND p.data_postagem >= now() - (%s || ' days')::interval
             LEFT JOIN tb_lkp_status_validacoes_postagens s ON s.id_status_validacao=p.status_validacao_id
            WHERE t.condominio_id=%s
            GROUP BY t.id_torre,t.nome_torre ORDER BY postagens_por_dia DESC,t.nome_torre""",
        [dias_baseline, dias_baseline, condominio_id],
    )


class RitmoDiarioTorresArgs(BaseModel):
    condominio_id: int = Field(gt=0)
    dias_baseline: int = Field(default=30, ge=7, le=365)


@tool("ritmo_diario_torres", args_schema=RitmoDiarioTorresArgs)
def ritmo_diario_torres(condominio_id: int, dias_baseline: int = 30) -> dict[str, Any]:
    """Calcula ritmo diário por torre diretamente do schema SQL fornecido."""
    try:
        return {"status": "ok", "condominio_id": condominio_id, "dias_baseline": dias_baseline, "torres": _ritmo_rows(condominio_id, dias_baseline)}
    except (PostgresUnavailable, psycopg2.Error) as exc:
        return db_failure(exc)


class SimularProjecaoArgs(BaseModel):
    condominio_id: int = Field(gt=0)
    dias_historico: int = Field(default=30, ge=7, le=365)
    dias_projecao: int = Field(default=30, ge=1, le=365)


@tool("simular_projecao_reciclagem", args_schema=SimularProjecaoArgs)
def simular_projecao_reciclagem(condominio_id: int, dias_historico: int = 30, dias_projecao: int = 30) -> dict[str, Any]:
    """Projeção linear baseada na média diária real de postagens aprovadas."""
    try:
        base = postgres_db.fetch_one(
            """SELECT COUNT(*)::bigint AS postagens_aprovadas,COALESCE(SUM(c.pontos_base),0)::bigint AS pontos_estimados
                 FROM tb_postagens p
                 JOIN tb_lkp_status_validacoes_postagens s ON s.id_status_validacao=p.status_validacao_id
                 JOIN tb_lkp_categorias_residuos c ON c.id_categoria=p.categoria_id
                WHERE p.condominio_id=%s AND s.nome_status='aprovada' AND c.permite_reciclagem=TRUE
                  AND p.data_postagem >= now() - (%s || ' days')::interval""",
            [condominio_id, dias_historico],
        ) or {}
        count = int(base.get("postagens_aprovadas") or 0)
        points = int(base.get("pontos_estimados") or 0)
        return {
            "status": "ok", "condominio_id": condominio_id,
            "historico": {"dias": dias_historico, **base},
            "projecao": {"dias": dias_projecao, "postagens_aprovadas": round(count / dias_historico * dias_projecao, 2), "pontos_estimados": round(points / dias_historico * dias_projecao, 2)},
            "metodo": "media_diaria_linear",
        }
    except (PostgresUnavailable, psycopg2.Error) as exc:
        return db_failure(exc)


class SimularTorreLiderancaArgs(BaseModel):
    condominio_id: int = Field(gt=0)
    torre_id: int = Field(gt=0)
    dias_baseline: int = Field(default=30, ge=7, le=365)


@tool("simular_torre_no_ritmo_da_lider", args_schema=SimularTorreLiderancaArgs)
def simular_torre_no_ritmo_da_lider(condominio_id: int, torre_id: int, dias_baseline: int = 30) -> dict[str, Any]:
    """Compara o ritmo da torre solicitada ao ritmo da líder no mesmo condomínio."""
    try:
        rows = _ritmo_rows(condominio_id, dias_baseline)
        target = next((row for row in rows if int(row["id_torre"]) == torre_id), None)
        leader = rows[0] if rows else None
        if target is None or leader is None:
            return {"status": "ok", "condominio_id": condominio_id, "torre_id": torre_id, "comparacao": None}
        current = float(target["postagens_por_dia"] or 0)
        leader_rate = float(leader["postagens_por_dia"] or 0)
        return {"status": "ok", "condominio_id": condominio_id, "torre": target, "lider": leader, "diferenca_postagens_por_dia": round(leader_rate-current, 4)}
    except (PostgresUnavailable, psycopg2.Error) as exc:
        return db_failure(exc)


TOOLS = [ritmo_diario_torres, simular_projecao_reciclagem, simular_torre_no_ritmo_da_lider]
