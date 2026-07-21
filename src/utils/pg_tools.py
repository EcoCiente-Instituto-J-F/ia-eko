"""
pg_tools.py
Ferramentas PostgreSQL do agente Analytics (EcoCiente)

Escopo: apenas o agente Analytics acessa o Postgres diretamente. FAQ e
Educacional são RAG (documentos locais / bases externas), Coletas consome
uma API própria, e Ranking/leaderboard fica no Redis (ZSET) — por isso
NENHUMA função aqui calcula posição/colocação de usuário, apenas
comparativos percentuais (acima/abaixo da média).

Perfis atendidos por essas ferramentas (conforme mapeamento agente x perfil):
- Síndico residencial / síndico comercial -> visão macro (condomínio, torres)
- Morador residencial / usuário comercial -> visão micro (individual)

Todas as ferramentas são somente leitura: quem cria/edita postagens,
votos, agendamentos etc. é a aplicação, não o chatbot.
"""

import os
import unicodedata
from datetime import date, datetime, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo

import psycopg2
from dotenv import load_dotenv
from langchain.tools import tool
from pydantic import BaseModel, Field

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

SP_TZ = ZoneInfo("America/Sao_Paulo")

POSTAGEM_BUSINESS_DATE_SQL = "(p.data_postagem AT TIME ZONE 'America/Sao_Paulo')::date"


def get_conn():
    if not DATABASE_URL:
        raise ValueError("A variável DATABASE_URL não foi encontrada no .env")
    return psycopg2.connect(DATABASE_URL)


def _safe_close(cur=None, conn=None):
    try:
        if cur:
            cur.close()
    except Exception:
        pass
    try:
        if conn:
            conn.close()
    except Exception:
        pass


def _normalize_text(text: Optional[str]) -> str:
    if not text:
        return ""
    text = text.strip().lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text


def _resolve_periodo(
    periodo: Optional[str],
    data_inicio: Optional[str],
    data_fim: Optional[str],
):
    """
    Se data_inicio/data_fim forem informados, eles têm prioridade.
    Caso contrário, resolve atalhos: hoje | semana | mes | mes_anterior | ano.
    Retorna (data_inicio, data_fim) como strings YYYY-MM-DD ou (None, None)
    para "todo o histórico".
    """
    if data_inicio or data_fim:
        return data_inicio, data_fim

    if not periodo:
        return None, None

    hoje = datetime.now(SP_TZ).date()
    p = _normalize_text(periodo)

    if p in ("hoje", "dia"):
        return hoje.isoformat(), hoje.isoformat()

    if p in ("semana", "ultima_semana", "7_dias", "ultimos_7_dias"):
        return (hoje - timedelta(days=6)).isoformat(), hoje.isoformat()

    if p in ("mes", "mes_atual", "ciclo_atual"):
        inicio = hoje.replace(day=1)
        return inicio.isoformat(), hoje.isoformat()

    if p in ("mes_anterior",):
        primeiro_dia_atual = hoje.replace(day=1)
        ultimo_dia_anterior = primeiro_dia_atual - timedelta(days=1)
        primeiro_dia_anterior = ultimo_dia_anterior.replace(day=1)
        return primeiro_dia_anterior.isoformat(), ultimo_dia_anterior.isoformat()

    if p in ("ano", "ano_atual"):
        inicio = hoje.replace(month=1, day=1)
        return inicio.isoformat(), hoje.isoformat()

    return None, None


def _date_filter_fragment(field_expr: str, data_inicio, data_fim, params: list) -> str:
    """
    Monta um trecho ' AND <field_expr> >= %s::date AND <field_expr> <= %s::date'
    e empilha os parâmetros na lista recebida (na mesma ordem que aparecem no SQL).
    Pode ser usado tanto em WHERE quanto em cláusulas ON (LEFT JOIN).
    """
    frag = ""
    if data_inicio:
        frag += f" AND {field_expr} >= %s::date"
        params.append(data_inicio)
    if data_fim:
        frag += f" AND {field_expr} <= %s::date"
        params.append(data_fim)
    return frag


def _resolve_categoria_id(cur, categoria_id: Optional[int], categoria_nome: Optional[str]) -> Optional[int]:
    if categoria_id:
        return int(categoria_id)
    if not categoria_nome:
        return None

    aliases = {
        "plastico": "plastico", "pet": "plastico", "garrafa pet": "plastico",
        "papel": "papel", "papelao": "papelao", "caixa": "papelao", "caixas": "papelao",
        "vidro": "vidro", "garrafa de vidro": "vidro",
        "metal": "metal", "aluminio": "metal", "lata": "metal", "latinha": "metal", "ferro": "metal",
        "organico": "organico", "compostagem": "organico", "resto de comida": "organico", "restos de comida": "organico",
        "eletronico": "eletronico", "eletronicos": "eletronico", "pilha": "eletronico", "bateria": "eletronico",
        "oleo": "oleo de cozinha", "oleo de cozinha": "oleo de cozinha",
    }
    alvo = _normalize_text(categoria_nome)
    alvo = aliases.get(alvo, alvo)

    cur.execute("SELECT id_categoria, nome_categoria FROM categorias_residuos;")
    rows = cur.fetchall()

    for cid, nome in rows:
        if _normalize_text(nome) == alvo:
            return cid

    for cid, nome in rows:
        nome_norm = _normalize_text(nome)
        if alvo in nome_norm or nome_norm in alvo:
            return cid

    return None


def _resolve_status_validacao_id(cur, status_nome: Optional[str]) -> Optional[int]:
    if not status_nome:
        return None

    aliases = {
        "aprovada": "aprovada", "aprovado": "aprovada", "aprovadas": "aprovada", "valida": "aprovada",
        "em_analise": "em_analise", "em analise": "em_analise", "analise": "em_analise",
        "pendente": "em_analise", "pendentes": "em_analise", "aguardando": "em_analise",
        "reprovada": "reprovada", "reprovado": "reprovada", "reprovadas": "reprovada",
        "rejeitada": "reprovada", "negada": "reprovada", "recusada": "reprovada",
    }
    alvo = _normalize_text(status_nome)
    alvo = aliases.get(alvo, alvo)

    cur.execute(
        "SELECT id_status_validacao FROM status_validacoes_postagens WHERE nome_status = %s LIMIT 1;",
        (alvo,)
    )
    row = cur.fetchone()
    return row[0] if row else None


def _resolve_contexto_usuario(cur, usuario_id: int) -> dict:
    """
    Descobre o vínculo do usuário com condomínio/torre:
      1) síndico -> condomínio sob sua gestão (sem torre específica)
      2) morador / usuário comercial -> unidade -> torre + condomínio
      3) fallback -> vínculo aprovado mais recente em usuarios_condominios
    """
    cur.execute(
        """
        SELECT c.id_condominio, NULL::integer AS torre_id, 'sindico' AS papel
        FROM sindicos s
        JOIN condominios c ON c.sindico_id = s.id_sindico
        WHERE s.usuario_id = %s
        LIMIT 1;
        """,
        (usuario_id,)
    )
    row = cur.fetchone()
    if row:
        return {"condominio_id": row[0], "torre_id": row[1], "papel": row[2]}

    cur.execute(
        """
        SELECT u.condominio_id, u.torre_id, 'morador' AS papel
        FROM moradores m
        JOIN unidades u ON u.id_unidade = m.unidade_id
        WHERE m.usuario_id = %s
        LIMIT 1;
        """,
        (usuario_id,)
    )
    row = cur.fetchone()
    if row:
        return {"condominio_id": row[0], "torre_id": row[1], "papel": row[2]}

    cur.execute(
        """
        SELECT condominio_id, NULL::integer, 'vinculo_generico'
        FROM usuarios_condominios
        WHERE usuario_id = %s AND aprovado = true
        ORDER BY data_entrada DESC
        LIMIT 1;
        """,
        (usuario_id,)
    )
    row = cur.fetchone()
    if row:
        return {"condominio_id": row[0], "torre_id": row[1], "papel": row[2]}

    return {"condominio_id": None, "torre_id": None, "papel": None}


# Tool: material_mais_reciclado

class MaterialMaisRecicladoArgs(BaseModel):
    condominio_id: Optional[int] = Field(default=None, description="Filtra por condomínio.")
    torre_id: Optional[int] = Field(default=None, description="Filtra por torre.")
    data_inicio: Optional[str] = Field(default=None, description="Data inicial local (YYYY-MM-DD), inclusiva.")
    data_fim: Optional[str] = Field(default=None, description="Data final local (YYYY-MM-DD), inclusiva.")
    periodo: Optional[str] = Field(default=None, description="Atalho: hoje | semana | mes | mes_anterior | ano.")
    somente_aprovadas: bool = Field(default=True, description="Considerar apenas postagens com status aprovada.")
    limite: int = Field(default=5, description="Quantidade de categorias no ranking de materiais.")


@tool("material_mais_reciclado", args_schema=MaterialMaisRecicladoArgs)
def material_mais_reciclado(
    condominio_id: Optional[int] = None,
    torre_id: Optional[int] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    periodo: Optional[str] = None,
    somente_aprovadas: bool = True,
    limite: int = 5,
) -> dict:
    """Retorna as categorias de resíduo mais recicladas por volume de postagens, com filtros opcionais de condomínio, torre e período."""
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()

        data_inicio, data_fim = _resolve_periodo(periodo, data_inicio, data_fim)

        sql = """
            SELECT
                cr.id_categoria,
                cr.nome_categoria,
                COUNT(*) AS total_postagens,
                COALESCE(SUM(cr.pontos_base), 0) AS pontos_estimados
            FROM postagens p
            JOIN categorias_residuos cr ON cr.id_categoria = p.categoria_id
            JOIN status_validacoes_postagens sv ON sv.id_status_validacao = p.status_validacao_id
            WHERE 1=1
        """
        params: List[object] = []

        if somente_aprovadas:
            sql += " AND sv.nome_status = 'aprovada'"

        if condominio_id:
            sql += " AND p.condominio_id = %s"
            params.append(condominio_id)

        if torre_id:
            sql += " AND p.torre_id = %s"
            params.append(torre_id)

        sql += _date_filter_fragment(POSTAGEM_BUSINESS_DATE_SQL, data_inicio, data_fim, params)

        sql += " GROUP BY cr.id_categoria, cr.nome_categoria ORDER BY total_postagens DESC LIMIT %s"
        params.append(limite)

        cur.execute(sql, tuple(params))
        rows = cur.fetchall()

        ranking = [
            {
                "categoria_id": r[0],
                "categoria_nome": r[1],
                "total_postagens": r[2],
                "pontos_estimados": r[3],
            }
            for r in rows
        ]

        return {
            "status": "ok",
            "periodo": {"data_inicio": data_inicio, "data_fim": data_fim},
            "ranking_materiais": ranking,
        }

    except Exception as e:
        if conn:
            conn.rollback()
        return {"status": "error", "message": str(e)}

    finally:
        _safe_close(cur, conn)


# 
# Tool: resumo_reciclagem_condominio (visão macro: síndico)

class ResumoCondominioArgs(BaseModel):
    condominio_id: int = Field(..., description="ID do condomínio.")
    data_inicio: Optional[str] = Field(default=None, description="Data inicial local (YYYY-MM-DD).")
    data_fim: Optional[str] = Field(default=None, description="Data final local (YYYY-MM-DD).")
    periodo: Optional[str] = Field(default=None, description="Atalho: hoje | semana | mes | mes_anterior | ano.")


@tool("resumo_reciclagem_condominio", args_schema=ResumoCondominioArgs)
def resumo_reciclagem_condominio(
    condominio_id: int,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    periodo: Optional[str] = None,
) -> dict:
    """
    Visão macro de reciclagem de um condomínio: volume de postagens por status,
    taxa de aprovação, material mais reciclado, participação dos moradores e
    comparativo entre torres. Uso típico: síndico residencial ou comercial.
    """
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()

        data_inicio, data_fim = _resolve_periodo(periodo, data_inicio, data_fim)

        # Totais por status
        params_status: List[object] = [condominio_id]
        frag_status = _date_filter_fragment(POSTAGEM_BUSINESS_DATE_SQL, data_inicio, data_fim, params_status)
        cur.execute(
            f"""
            SELECT sv.nome_status, COUNT(*)
            FROM postagens p
            JOIN status_validacoes_postagens sv ON sv.id_status_validacao = p.status_validacao_id
            WHERE p.condominio_id = %s {frag_status}
            GROUP BY sv.nome_status;
            """,
            tuple(params_status)
        )
        totais_status = {nome: qtd for nome, qtd in cur.fetchall()}
        total_postagens = sum(totais_status.values())
        aprovadas = totais_status.get("aprovada", 0)
        taxa_aprovacao = round((aprovadas / total_postagens * 100), 2) if total_postagens else 0.0

        # Categoria mais reciclada (aprovadas)
        params_top: List[object] = [condominio_id]
        frag_top = _date_filter_fragment(POSTAGEM_BUSINESS_DATE_SQL, data_inicio, data_fim, params_top)
        cur.execute(
            f"""
            SELECT cr.nome_categoria, COUNT(*) AS qtd, COALESCE(SUM(cr.pontos_base), 0) AS pontos
            FROM postagens p
            JOIN categorias_residuos cr ON cr.id_categoria = p.categoria_id
            JOIN status_validacoes_postagens sv ON sv.id_status_validacao = p.status_validacao_id
            WHERE p.condominio_id = %s AND sv.nome_status = 'aprovada' {frag_top}
            GROUP BY cr.nome_categoria
            ORDER BY qtd DESC
            LIMIT 1;
            """,
            tuple(params_top)
        )
        top_row = cur.fetchone()
        categoria_mais_reciclada = None
        if top_row:
            categoria_mais_reciclada = {
                "categoria_nome": top_row[0],
                "total_postagens": top_row[1],
                "pontos_estimados": top_row[2],
            }

        # Pontos totais estimados (aprovadas)
        params_pts: List[object] = [condominio_id]
        frag_pts = _date_filter_fragment(POSTAGEM_BUSINESS_DATE_SQL, data_inicio, data_fim, params_pts)
        cur.execute(
            f"""
            SELECT COALESCE(SUM(cr.pontos_base), 0)
            FROM postagens p
            JOIN categorias_residuos cr ON cr.id_categoria = p.categoria_id
            JOIN status_validacoes_postagens sv ON sv.id_status_validacao = p.status_validacao_id
            WHERE p.condominio_id = %s AND sv.nome_status = 'aprovada' {frag_pts};
            """,
            tuple(params_pts)
        )
        pontos_totais_estimados = int(cur.fetchone()[0])

        # Participação: moradores/usuários comerciais do condomínio vs. quem postou no período
        cur.execute(
            """
            SELECT COUNT(*)
            FROM moradores m
            JOIN unidades u ON u.id_unidade = m.unidade_id
            WHERE u.condominio_id = %s;
            """,
            (condominio_id,)
        )
        moradores_total = cur.fetchone()[0]

        params_part: List[object] = [condominio_id]
        frag_part = _date_filter_fragment(POSTAGEM_BUSINESS_DATE_SQL, data_inicio, data_fim, params_part)
        cur.execute(
            f"""
            SELECT COUNT(DISTINCT p.usuario_id)
            FROM postagens p
            WHERE p.condominio_id = %s {frag_part};
            """,
            tuple(params_part)
        )
        moradores_participantes = cur.fetchone()[0]
        taxa_participacao = round((moradores_participantes / moradores_total * 100), 2) if moradores_total else 0.0

        # Comparativo por torre
        params_torres: List[object] = []
        frag_torres_on = _date_filter_fragment(POSTAGEM_BUSINESS_DATE_SQL, data_inicio, data_fim, params_torres)
        params_torres.append(condominio_id)
        cur.execute(
            f"""
            SELECT
                t.id_torre,
                t.nome_torre,
                COUNT(p.id_postagem) AS total_postagens,
                COUNT(*) FILTER (WHERE sv.nome_status = 'aprovada') AS aprovadas,
                COALESCE(SUM(cr.pontos_base) FILTER (WHERE sv.nome_status = 'aprovada'), 0) AS pontos_estimados
            FROM torres t
            LEFT JOIN postagens p ON p.torre_id = t.id_torre {frag_torres_on}
            LEFT JOIN status_validacoes_postagens sv ON sv.id_status_validacao = p.status_validacao_id
            LEFT JOIN categorias_residuos cr ON cr.id_categoria = p.categoria_id
            WHERE t.condominio_id = %s
            GROUP BY t.id_torre, t.nome_torre
            ORDER BY total_postagens DESC;
            """,
            tuple(params_torres)
        )
        torres = [
            {
                "torre_id": r[0],
                "torre_nome": r[1],
                "total_postagens": r[2],
                "aprovadas": r[3],
                "pontos_estimados": r[4],
            }
            for r in cur.fetchall()
        ]

        return {
            "status": "ok",
            "condominio_id": condominio_id,
            "periodo": {"data_inicio": data_inicio, "data_fim": data_fim},
            "postagens_por_status": totais_status,
            "total_postagens": total_postagens,
            "taxa_aprovacao": taxa_aprovacao,
            "pontos_totais_estimados": pontos_totais_estimados,
            "categoria_mais_reciclada": categoria_mais_reciclada,
            "moradores_total": moradores_total,
            "moradores_participantes": moradores_participantes,
            "taxa_participacao": taxa_participacao,
            "comparativo_torres": torres,
        }

    except Exception as e:
        if conn:
            conn.rollback()
        return {"status": "error", "message": str(e)}

    finally:
        _safe_close(cur, conn)

TOOLS = [material_mais_reciclado,
    resumo_reciclagem_condominio,
    
]
