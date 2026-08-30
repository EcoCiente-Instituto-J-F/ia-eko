from __future__ import annotations

from typing import Any

import psycopg2
from langchain.tools import tool
from pydantic import BaseModel, Field

from src.agents.analytics.tools.common import date_filters, db_failure, resolve_period
from src.database.postgres import PostgresUnavailable, postgres_db


class MaterialMaisRecicladoArgs(BaseModel):
    condominio_id: int | None = Field(default=None, gt=0)
    torre_id: int | None = Field(default=None, gt=0)
    data_inicio: str | None = None
    data_fim: str | None = None
    periodo: str | None = None
    limite: int = Field(default=5, ge=1, le=20)


@tool("material_mais_reciclado", args_schema=MaterialMaisRecicladoArgs)
def material_mais_reciclado(condominio_id: int | None = None, torre_id: int | None = None, data_inicio: str | None = None, data_fim: str | None = None, periodo: str | None = None, limite: int = 5) -> dict[str, Any]:
    """Lista categorias mais recicladas considerando somente postagens aprovadas."""
    try:
        data_inicio, data_fim = resolve_period(periodo, data_inicio, data_fim)
        params: list[Any] = []
        sql = """
        SELECT c.id_categoria, c.nome_categoria,
               COUNT(*)::bigint AS total_postagens,
               COALESCE(SUM(c.pontos_base), 0)::bigint AS pontos_estimados
          FROM tb_postagens p
          JOIN tb_lkp_categorias_residuos c ON c.id_categoria = p.categoria_id
          JOIN tb_lkp_status_validacoes_postagens s ON s.id_status_validacao = p.status_validacao_id
         WHERE s.nome_status = 'aprovada' AND c.permite_reciclagem = TRUE
        """
        if condominio_id:
            sql += " AND p.condominio_id = %s"; params.append(condominio_id)
        if torre_id:
            sql += " AND p.torre_id = %s"; params.append(torre_id)
        sql += date_filters("p.data_postagem", data_inicio, data_fim, params)
        sql += " GROUP BY c.id_categoria, c.nome_categoria ORDER BY total_postagens DESC, c.nome_categoria LIMIT %s"
        params.append(limite)
        rows = postgres_db.fetch_all(sql, params)
        return {"status": "ok", "periodo": {"data_inicio": data_inicio, "data_fim": data_fim}, "ranking_materiais": rows}
    except (PostgresUnavailable, psycopg2.Error) as exc:
        return db_failure(exc)


class ResumoCondominioArgs(BaseModel):
    condominio_id: int = Field(gt=0)
    data_inicio: str | None = None
    data_fim: str | None = None
    periodo: str | None = None


@tool("resumo_reciclagem_condominio", args_schema=ResumoCondominioArgs)
def resumo_reciclagem_condominio(condominio_id: int, data_inicio: str | None = None, data_fim: str | None = None, periodo: str | None = None) -> dict[str, Any]:
    """Resumo macro do condomínio: status, participantes, pontos estimados e categoria líder."""
    try:
        data_inicio, data_fim = resolve_period(periodo, data_inicio, data_fim)
        params: list[Any] = [condominio_id]
        filt = date_filters("p.data_postagem", data_inicio, data_fim, params)
        summary = postgres_db.fetch_one(
            """
            SELECT COUNT(*)::bigint AS total_postagens,
                   COUNT(*) FILTER (WHERE s.nome_status='aprovada')::bigint AS aprovadas,
                   COUNT(*) FILTER (WHERE s.nome_status='reprovada')::bigint AS reprovadas,
                   COUNT(*) FILTER (WHERE s.nome_status='em_analise')::bigint AS em_analise,
                   COUNT(DISTINCT p.usuario_id)::bigint AS usuarios_participantes,
                   COALESCE(SUM(c.pontos_base) FILTER (WHERE s.nome_status='aprovada' AND c.permite_reciclagem=TRUE),0)::bigint AS pontos_estimados,
                   ROUND(100.0 * COUNT(*) FILTER (WHERE s.nome_status='aprovada') / NULLIF(COUNT(*),0), 2) AS taxa_aprovacao_percentual
              FROM tb_postagens p
              JOIN tb_lkp_status_validacoes_postagens s ON s.id_status_validacao=p.status_validacao_id
              JOIN tb_lkp_categorias_residuos c ON c.id_categoria=p.categoria_id
             WHERE p.condominio_id=%s
            """ + filt,
            params,
        ) or {}
        top_params: list[Any] = [condominio_id]
        top_filter = date_filters("p.data_postagem", data_inicio, data_fim, top_params)
        top = postgres_db.fetch_one(
            """
            SELECT c.id_categoria, c.nome_categoria, COUNT(*)::bigint AS total_postagens
              FROM tb_postagens p
              JOIN tb_lkp_categorias_residuos c ON c.id_categoria=p.categoria_id
              JOIN tb_lkp_status_validacoes_postagens s ON s.id_status_validacao=p.status_validacao_id
             WHERE p.condominio_id=%s AND s.nome_status='aprovada' AND c.permite_reciclagem=TRUE
            """ + top_filter + " GROUP BY c.id_categoria,c.nome_categoria ORDER BY total_postagens DESC, c.nome_categoria LIMIT 1",
            top_params,
        )
        return {"status": "ok", "condominio_id": condominio_id, "periodo": {"data_inicio": data_inicio, "data_fim": data_fim}, "resumo": summary, "categoria_top": top}
    except (PostgresUnavailable, psycopg2.Error) as exc:
        return db_failure(exc)


class ResumoMoradorArgs(BaseModel):
    usuario_id: int = Field(gt=0)
    data_inicio: str | None = None
    data_fim: str | None = None
    periodo: str | None = None


@tool("resumo_reciclagem_morador", args_schema=ResumoMoradorArgs)
def resumo_reciclagem_morador(usuario_id: int, data_inicio: str | None = None, data_fim: str | None = None, periodo: str | None = None) -> dict[str, Any]:
    """Resumo individual do próprio usuário, sem expor dados identificáveis de terceiros."""
    try:
        data_inicio, data_fim = resolve_period(periodo, data_inicio, data_fim)
        params: list[Any] = [usuario_id]
        filt = date_filters("p.data_postagem", data_inicio, data_fim, params)
        summary = postgres_db.fetch_one(
            """
            SELECT COUNT(*)::bigint AS total_postagens,
                   COUNT(*) FILTER (WHERE s.nome_status='aprovada')::bigint AS aprovadas,
                   COUNT(*) FILTER (WHERE s.nome_status='reprovada')::bigint AS reprovadas,
                   COUNT(*) FILTER (WHERE s.nome_status='em_analise')::bigint AS em_analise,
                   COALESCE(SUM(c.pontos_base) FILTER (WHERE s.nome_status='aprovada' AND c.permite_reciclagem=TRUE),0)::bigint AS pontos_estimados,
                   ROUND(100.0 * COUNT(*) FILTER (WHERE s.nome_status='aprovada') / NULLIF(COUNT(*),0), 2) AS taxa_aprovacao_percentual
              FROM tb_postagens p
              JOIN tb_lkp_status_validacoes_postagens s ON s.id_status_validacao=p.status_validacao_id
              JOIN tb_lkp_categorias_residuos c ON c.id_categoria=p.categoria_id
             WHERE p.usuario_id=%s
            """ + filt,
            params,
        ) or {}
        context = postgres_db.fetch_one(
            """SELECT m.condominio_id, r.trust_score, n.nome_nivel AS nivel_confianca
                 FROM tb_moradores m
                 LEFT JOIN tb_rel_usuarios_condominios r ON r.usuario_id=m.usuario_id AND r.condominio_id=m.condominio_id
                 LEFT JOIN tb_lkp_niveis_confianca n ON n.id_nivel_confianca=r.nivel_confianca_id
                WHERE m.usuario_id=%s LIMIT 1""",
            [usuario_id],
        )
        return {"status": "ok", "usuario_id": usuario_id, "periodo": {"data_inicio": data_inicio, "data_fim": data_fim}, "resumo": summary, "contexto": context}
    except (PostgresUnavailable, psycopg2.Error) as exc:
        return db_failure(exc)


class CompararTorresArgs(BaseModel):
    condominio_id: int = Field(gt=0)
    data_inicio: str | None = None
    data_fim: str | None = None
    periodo: str | None = None
    limite: int = Field(default=20, ge=1, le=100)


@tool("comparar_torres", args_schema=CompararTorresArgs)
def comparar_torres(condominio_id: int, data_inicio: str | None = None, data_fim: str | None = None, periodo: str | None = None, limite: int = 20) -> dict[str, Any]:
    """Compara torres do mesmo condomínio usando somente postagens aprovadas."""
    try:
        data_inicio, data_fim = resolve_period(periodo, data_inicio, data_fim)
        params: list[Any] = []
        join_filter = date_filters("p.data_postagem", data_inicio, data_fim, params)
        params.extend([condominio_id, limite])
        rows = postgres_db.fetch_all(
            """
            SELECT t.id_torre, t.nome_torre,
                   COUNT(p.id_postagem) FILTER (WHERE s.nome_status='aprovada' AND c.permite_reciclagem=TRUE)::bigint AS postagens_aprovadas,
                   COUNT(DISTINCT p.usuario_id) FILTER (WHERE s.nome_status='aprovada' AND c.permite_reciclagem=TRUE)::bigint AS usuarios_participantes,
                   COALESCE(SUM(c.pontos_base) FILTER (WHERE s.nome_status='aprovada' AND c.permite_reciclagem=TRUE),0)::bigint AS pontos_estimados
              FROM tb_torres t
              LEFT JOIN tb_postagens p ON p.torre_id=t.id_torre
            """ + join_filter + """
              LEFT JOIN tb_lkp_status_validacoes_postagens s ON s.id_status_validacao=p.status_validacao_id
              LEFT JOIN tb_lkp_categorias_residuos c ON c.id_categoria=p.categoria_id
             WHERE t.condominio_id=%s
             GROUP BY t.id_torre,t.nome_torre ORDER BY postagens_aprovadas DESC,t.nome_torre LIMIT %s
            """,
            params,
        )
        return {"status": "ok", "condominio_id": condominio_id, "periodo": {"data_inicio": data_inicio, "data_fim": data_fim}, "torres": rows}
    except (PostgresUnavailable, psycopg2.Error) as exc:
        return db_failure(exc)


class CompararPeriodosArgs(BaseModel):
    condominio_id: int = Field(gt=0)
    periodo_a_inicio: str
    periodo_a_fim: str
    periodo_b_inicio: str
    periodo_b_fim: str


@tool("comparar_periodos", args_schema=CompararPeriodosArgs)
def comparar_periodos(condominio_id: int, periodo_a_inicio: str, periodo_a_fim: str, periodo_b_inicio: str, periodo_b_fim: str) -> dict[str, Any]:
    """Compara dois períodos explícitos do mesmo condomínio."""
    try:
        query = """
        SELECT COUNT(*)::bigint AS postagens_aprovadas,
               COUNT(DISTINCT p.usuario_id)::bigint AS usuarios_participantes,
               COALESCE(SUM(c.pontos_base),0)::bigint AS pontos_estimados
          FROM tb_postagens p
          JOIN tb_lkp_status_validacoes_postagens s ON s.id_status_validacao=p.status_validacao_id
          JOIN tb_lkp_categorias_residuos c ON c.id_categoria=p.categoria_id
         WHERE p.condominio_id=%s AND s.nome_status='aprovada' AND c.permite_reciclagem=TRUE
           AND (p.data_postagem AT TIME ZONE 'America/Sao_Paulo')::date BETWEEN %s::date AND %s::date
        """
        a = postgres_db.fetch_one(query, [condominio_id, periodo_a_inicio, periodo_a_fim]) or {}
        b = postgres_db.fetch_one(query, [condominio_id, periodo_b_inicio, periodo_b_fim]) or {}
        av, bv = int(a.get("postagens_aprovadas") or 0), int(b.get("postagens_aprovadas") or 0)
        variacao = None if av == 0 else round((bv - av) * 100.0 / av, 2)
        return {"status": "ok", "condominio_id": condominio_id, "periodo_a": a, "periodo_b": b, "variacao_postagens_percentual": variacao}
    except (PostgresUnavailable, psycopg2.Error) as exc:
        return db_failure(exc)


class TaxaAprovacaoArgs(BaseModel):
    condominio_id: int | None = Field(default=None, gt=0)
    usuario_id: int | None = Field(default=None, gt=0)
    data_inicio: str | None = None
    data_fim: str | None = None
    periodo: str | None = None


@tool("taxa_aprovacao_postagens", args_schema=TaxaAprovacaoArgs)
def taxa_aprovacao_postagens(condominio_id: int | None = None, usuario_id: int | None = None, data_inicio: str | None = None, data_fim: str | None = None, periodo: str | None = None) -> dict[str, Any]:
    """Calcula distribuição de status e taxa de aprovação das postagens."""
    try:
        data_inicio, data_fim = resolve_period(periodo, data_inicio, data_fim)
        params: list[Any] = []
        sql = """SELECT COUNT(*)::bigint AS total,
                        COUNT(*) FILTER(WHERE s.nome_status='aprovada')::bigint AS aprovadas,
                        COUNT(*) FILTER(WHERE s.nome_status='reprovada')::bigint AS reprovadas,
                        COUNT(*) FILTER(WHERE s.nome_status='em_analise')::bigint AS em_analise,
                        ROUND(100.0*COUNT(*) FILTER(WHERE s.nome_status='aprovada')/NULLIF(COUNT(*),0),2) AS taxa_aprovacao_percentual
                   FROM tb_postagens p JOIN tb_lkp_status_validacoes_postagens s ON s.id_status_validacao=p.status_validacao_id WHERE 1=1"""
        if condominio_id:
            sql += " AND p.condominio_id=%s"; params.append(condominio_id)
        if usuario_id:
            sql += " AND p.usuario_id=%s"; params.append(usuario_id)
        sql += date_filters("p.data_postagem", data_inicio, data_fim, params)
        return {"status": "ok", "periodo": {"data_inicio": data_inicio, "data_fim": data_fim}, "metricas": postgres_db.fetch_one(sql, params) or {}}
    except (PostgresUnavailable, psycopg2.Error) as exc:
        return db_failure(exc)


class EvolucaoReciclagemArgs(BaseModel):
    condominio_id: int = Field(gt=0)
    data_inicio: str | None = None
    data_fim: str | None = None
    periodo: str | None = None
    granularidade: str = Field(default="dia", pattern="^(dia|semana|mes)$")


@tool("evolucao_reciclagem_periodo", args_schema=EvolucaoReciclagemArgs)
def evolucao_reciclagem_periodo(condominio_id: int, data_inicio: str | None = None, data_fim: str | None = None, periodo: str | None = None, granularidade: str = "dia") -> dict[str, Any]:
    """Série temporal de postagens aprovadas do condomínio."""
    try:
        data_inicio, data_fim = resolve_period(periodo, data_inicio, data_fim)
        trunc = {"dia": "day", "semana": "week", "mes": "month"}[granularidade]
        params: list[Any] = [condominio_id]
        filt = date_filters("p.data_postagem", data_inicio, data_fim, params)
        rows = postgres_db.fetch_all(
            f"""SELECT date_trunc('{trunc}', p.data_postagem AT TIME ZONE 'America/Sao_Paulo')::date AS periodo,
                       COUNT(*)::bigint AS postagens_aprovadas, COUNT(DISTINCT p.usuario_id)::bigint AS usuarios_participantes,
                       COALESCE(SUM(c.pontos_base),0)::bigint AS pontos_estimados
                  FROM tb_postagens p
                  JOIN tb_lkp_status_validacoes_postagens s ON s.id_status_validacao=p.status_validacao_id
                  JOIN tb_lkp_categorias_residuos c ON c.id_categoria=p.categoria_id
                 WHERE p.condominio_id=%s AND s.nome_status='aprovada' AND c.permite_reciclagem=TRUE""" + filt + " GROUP BY 1 ORDER BY 1",
            params,
        )
        return {"status": "ok", "condominio_id": condominio_id, "granularidade": granularidade, "serie": rows}
    except (PostgresUnavailable, psycopg2.Error) as exc:
        return db_failure(exc)


class ListarPostagensArgs(BaseModel):
    usuario_id: int | None = Field(default=None, gt=0)
    condominio_id: int | None = Field(default=None, gt=0)
    status: str | None = None
    data_inicio: str | None = None
    data_fim: str | None = None
    periodo: str | None = None
    limite: int = Field(default=20, ge=1, le=100)


@tool("listar_postagens", args_schema=ListarPostagensArgs)
def listar_postagens(usuario_id: int | None = None, condominio_id: int | None = None, status: str | None = None, data_inicio: str | None = None, data_fim: str | None = None, periodo: str | None = None, limite: int = 20) -> dict[str, Any]:
    """Lista postagens sem expor nome/e-mail de terceiros; o agente deve respeitar o escopo autorizado."""
    try:
        data_inicio, data_fim = resolve_period(periodo, data_inicio, data_fim)
        params: list[Any] = []
        sql = """SELECT p.id_postagem,p.usuario_id,p.condominio_id,p.torre_id,c.nome_categoria,s.nome_status AS status_validacao,
                        p.data_postagem,p.saldo_confianca,p.triagem_automatica_aprovada,p.triagem_automatica_confianca
                   FROM tb_postagens p
                   JOIN tb_lkp_categorias_residuos c ON c.id_categoria=p.categoria_id
                   JOIN tb_lkp_status_validacoes_postagens s ON s.id_status_validacao=p.status_validacao_id WHERE 1=1"""
        if usuario_id:
            sql += " AND p.usuario_id=%s"; params.append(usuario_id)
        if condominio_id:
            sql += " AND p.condominio_id=%s"; params.append(condominio_id)
        if status:
            sql += " AND s.nome_status=%s"; params.append(status)
        sql += date_filters("p.data_postagem", data_inicio, data_fim, params)
        sql += " ORDER BY p.data_postagem DESC LIMIT %s"; params.append(limite)
        return {"status": "ok", "postagens": postgres_db.fetch_all(sql, params)}
    except (PostgresUnavailable, psycopg2.Error) as exc:
        return db_failure(exc)


TOOLS = [
    material_mais_reciclado,
    resumo_reciclagem_condominio,
    resumo_reciclagem_morador,
    comparar_torres,
    comparar_periodos,
    taxa_aprovacao_postagens,
    evolucao_reciclagem_periodo,
    listar_postagens,
]
