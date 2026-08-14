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

Simulação "e se" (simular_projecao_reciclagem / simular_torre_no_ritmo_da_lider
/ ritmo_diario_torres): chamam as functions fn_projecao_reciclagem e
fn_ritmo_diario_torres (ver ecociente_simulacao_e_se.sql). A lógica de
projeção fica no banco (PL/pgSQL), não em Python — evita duplicar regra de
negócio entre a function e a tool.

Nomenclatura de tabelas alinhada a ecociente_schema.sql:
tb_ (entidades), tb_lkp_ (domínios/lookups), tb_rel_ (relacionamentos N:N
ou vínculos com atributos).

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

    cur.execute("SELECT id_categoria, nome_categoria FROM tb_lkp_categorias_residuos;")
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
        "SELECT id_status_validacao FROM tb_lkp_status_validacoes_postagens WHERE nome_status = %s LIMIT 1;",
        (alvo,)
    )
    row = cur.fetchone()
    return row[0] if row else None


def _resolve_contexto_usuario(cur, usuario_id: int) -> dict:
    """
    Descobre o vínculo do usuário com condomínio/torre:
      1) síndico -> condomínio sob sua gestão (sem torre específica)
      2) morador / usuário comercial -> unidade -> torre + condomínio
      3) fallback -> vínculo aprovado mais recente em tb_rel_usuarios_condominios
    """
    cur.execute(
        """
        SELECT c.id_condominio, NULL::integer AS torre_id, 'sindico' AS papel
        FROM tb_sindicos s
        JOIN tb_condominios c ON c.sindico_id = s.id_sindico
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
        FROM tb_moradores m
        JOIN tb_unidades u ON u.id_unidade = m.unidade_id
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
        FROM tb_rel_usuarios_condominios
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
            FROM tb_postagens p
            JOIN tb_lkp_categorias_residuos cr ON cr.id_categoria = p.categoria_id
            JOIN tb_lkp_status_validacoes_postagens sv ON sv.id_status_validacao = p.status_validacao_id
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
            FROM tb_postagens p
            JOIN tb_lkp_status_validacoes_postagens sv ON sv.id_status_validacao = p.status_validacao_id
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
            FROM tb_postagens p
            JOIN tb_lkp_categorias_residuos cr ON cr.id_categoria = p.categoria_id
            JOIN tb_lkp_status_validacoes_postagens sv ON sv.id_status_validacao = p.status_validacao_id
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
            FROM tb_postagens p
            JOIN tb_lkp_categorias_residuos cr ON cr.id_categoria = p.categoria_id
            JOIN tb_lkp_status_validacoes_postagens sv ON sv.id_status_validacao = p.status_validacao_id
            WHERE p.condominio_id = %s AND sv.nome_status = 'aprovada' {frag_pts};
            """,
            tuple(params_pts)
        )
        pontos_totais_estimados = int(cur.fetchone()[0])

        # Participação: moradores/usuários comerciais do condomínio vs. quem postou no período
        cur.execute(
            """
            SELECT COUNT(*)
            FROM tb_moradores m
            JOIN tb_unidades u ON u.id_unidade = m.unidade_id
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
            FROM tb_postagens p
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
            FROM tb_torres t
            LEFT JOIN tb_postagens p ON p.torre_id = t.id_torre {frag_torres_on}
            LEFT JOIN tb_lkp_status_validacoes_postagens sv ON sv.id_status_validacao = p.status_validacao_id
            LEFT JOIN tb_lkp_categorias_residuos cr ON cr.id_categoria = p.categoria_id
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


# Tool: resumo_reciclagem_morador (visão micro: individual)

class ResumoMoradorArgs(BaseModel):
    usuario_id: int = Field(..., description="ID do usuário (morador residencial ou usuário comercial).")
    data_inicio: Optional[str] = Field(default=None, description="Data inicial local (YYYY-MM-DD).")
    data_fim: Optional[str] = Field(default=None, description="Data final local (YYYY-MM-DD).")
    periodo: Optional[str] = Field(default=None, description="Atalho: hoje | semana | mes | mes_anterior | ano.")


@tool("resumo_reciclagem_morador", args_schema=ResumoMoradorArgs)
def resumo_reciclagem_morador(
    usuario_id: int,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    periodo: Optional[str] = None,
) -> dict:
    """
    Visão individual (micro) de reciclagem de um morador/usuário comercial:
    total de postagens por status, pontos estimados, categoria favorita, e
    comparação percentual com a média da sua torre e do condomínio no mesmo
    período. Não calcula posição/colocação — isso é responsabilidade do
    ranking em Redis.
    """
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()

        data_inicio, data_fim = _resolve_periodo(periodo, data_inicio, data_fim)

        contexto = _resolve_contexto_usuario(cur, usuario_id)
        condominio_id = contexto["condominio_id"]
        torre_id = contexto["torre_id"]

        if not condominio_id:
            return {"status": "error", "message": "Usuário não está vinculado a nenhum condomínio."}

        # Postagens por status
        params_status: List[object] = [usuario_id]
        frag_status = _date_filter_fragment(POSTAGEM_BUSINESS_DATE_SQL, data_inicio, data_fim, params_status)
        cur.execute(
            f"""
            SELECT sv.nome_status, COUNT(*)
            FROM tb_postagens p
            JOIN tb_lkp_status_validacoes_postagens sv ON sv.id_status_validacao = p.status_validacao_id
            WHERE p.usuario_id = %s {frag_status}
            GROUP BY sv.nome_status;
            """,
            tuple(params_status)
        )
        totais_status = {nome: qtd for nome, qtd in cur.fetchall()}
        total_postagens = sum(totais_status.values())

        # Pontos estimados do usuário (aprovadas)
        params_pts: List[object] = [usuario_id]
        frag_pts = _date_filter_fragment(POSTAGEM_BUSINESS_DATE_SQL, data_inicio, data_fim, params_pts)
        cur.execute(
            f"""
            SELECT COALESCE(SUM(cr.pontos_base), 0)
            FROM tb_postagens p
            JOIN tb_lkp_categorias_residuos cr ON cr.id_categoria = p.categoria_id
            JOIN tb_lkp_status_validacoes_postagens sv ON sv.id_status_validacao = p.status_validacao_id
            WHERE p.usuario_id = %s AND sv.nome_status = 'aprovada' {frag_pts};
            """,
            tuple(params_pts)
        )
        pontos_usuario = int(cur.fetchone()[0])

        # Categoria favorita
        params_fav: List[object] = [usuario_id]
        frag_fav = _date_filter_fragment(POSTAGEM_BUSINESS_DATE_SQL, data_inicio, data_fim, params_fav)
        cur.execute(
            f"""
            SELECT cr.nome_categoria, COUNT(*) AS qtd
            FROM tb_postagens p
            JOIN tb_lkp_categorias_residuos cr ON cr.id_categoria = p.categoria_id
            WHERE p.usuario_id = %s {frag_fav}
            GROUP BY cr.nome_categoria
            ORDER BY qtd DESC
            LIMIT 1;
            """,
            tuple(params_fav)
        )
        row = cur.fetchone()
        categoria_favorita = row[0] if row else None

        def _media_pontos_por_usuario(campo: str, valor) -> float:
            params: List[object] = [valor]
            frag = _date_filter_fragment(POSTAGEM_BUSINESS_DATE_SQL, data_inicio, data_fim, params)
            cur.execute(
                f"""
                SELECT COALESCE(AVG(pontos_por_usuario.pontos), 0)
                FROM (
                    SELECT p.usuario_id, COALESCE(SUM(cr.pontos_base), 0) AS pontos
                    FROM tb_postagens p
                    JOIN tb_lkp_categorias_residuos cr ON cr.id_categoria = p.categoria_id
                    JOIN tb_lkp_status_validacoes_postagens sv ON sv.id_status_validacao = p.status_validacao_id
                    WHERE {campo} = %s AND sv.nome_status = 'aprovada' {frag}
                    GROUP BY p.usuario_id
                ) AS pontos_por_usuario;
                """,
                tuple(params)
            )
            return float(cur.fetchone()[0])

        media_torre = _media_pontos_por_usuario("p.torre_id", torre_id) if torre_id else None
        media_condominio = _media_pontos_por_usuario("p.condominio_id", condominio_id)

        def _comparativo(valor, media):
            if media is None or media == 0:
                return None
            return round(((valor - media) / media) * 100, 2)

        return {
            "status": "ok",
            "usuario_id": usuario_id,
            "condominio_id": condominio_id,
            "torre_id": torre_id,
            "periodo": {"data_inicio": data_inicio, "data_fim": data_fim},
            "postagens_por_status": totais_status,
            "total_postagens": total_postagens,
            "pontos_estimados": pontos_usuario,
            "categoria_favorita": categoria_favorita,
            "media_pontos_torre": media_torre,
            "comparativo_percentual_torre": _comparativo(pontos_usuario, media_torre),
            "media_pontos_condominio": media_condominio,
            "comparativo_percentual_condominio": _comparativo(pontos_usuario, media_condominio),
        }

    except Exception as e:
        if conn:
            conn.rollback()
        return {"status": "error", "message": str(e)}

    finally:
        _safe_close(cur, conn)


# Tool: comparar_torres

class CompararTorresArgs(BaseModel):
    condominio_id: int = Field(..., description="ID do condomínio.")
    torre_ids: Optional[List[int]] = Field(
        default=None,
        description="IDs de torres a comparar. Se omitido, compara todas as torres do condomínio."
    )
    data_inicio: Optional[str] = Field(default=None, description="Data inicial local (YYYY-MM-DD).")
    data_fim: Optional[str] = Field(default=None, description="Data final local (YYYY-MM-DD).")
    periodo: Optional[str] = Field(default=None, description="Atalho: hoje | semana | mes | mes_anterior | ano.")


@tool("comparar_torres", args_schema=CompararTorresArgs)
def comparar_torres(
    condominio_id: int,
    torre_ids: Optional[List[int]] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    periodo: Optional[str] = None,
) -> dict:
    """Compara torres de um condomínio em volume de postagens, taxa de aprovação, pontos estimados e participação, em um período."""
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()

        data_inicio, data_fim = _resolve_periodo(periodo, data_inicio, data_fim)

        params: List[object] = []
        frag_on = _date_filter_fragment(POSTAGEM_BUSINESS_DATE_SQL, data_inicio, data_fim, params)

        sql = f"""
            SELECT
                t.id_torre,
                t.nome_torre,
                (
                    SELECT COUNT(*)
                    FROM tb_moradores m
                    JOIN tb_unidades u ON u.id_unidade = m.unidade_id
                    WHERE u.torre_id = t.id_torre
                ) AS moradores_total,
                COUNT(DISTINCT p.usuario_id) AS moradores_participantes,
                COUNT(p.id_postagem) AS postagens_total,
                COUNT(*) FILTER (WHERE sv.nome_status = 'aprovada') AS postagens_aprovadas,
                COALESCE(SUM(cr.pontos_base) FILTER (WHERE sv.nome_status = 'aprovada'), 0) AS pontos_estimados
            FROM tb_torres t
            LEFT JOIN tb_postagens p ON p.torre_id = t.id_torre {frag_on}
            LEFT JOIN tb_lkp_status_validacoes_postagens sv ON sv.id_status_validacao = p.status_validacao_id
            LEFT JOIN tb_lkp_categorias_residuos cr ON cr.id_categoria = p.categoria_id
            WHERE t.condominio_id = %s
        """
        params.append(condominio_id)

        if torre_ids:
            sql += " AND t.id_torre = ANY(%s)"
            params.append(torre_ids)

        sql += " GROUP BY t.id_torre, t.nome_torre ORDER BY postagens_total DESC;"

        cur.execute(sql, tuple(params))
        rows = cur.fetchall()

        torres = []
        for r in rows:
            total = r[4]
            aprovadas = r[5]
            taxa_aprovacao = round((aprovadas / total * 100), 2) if total else 0.0
            torres.append({
                "torre_id": r[0],
                "torre_nome": r[1],
                "moradores_total": r[2],
                "moradores_participantes": r[3],
                "postagens_total": total,
                "postagens_aprovadas": aprovadas,
                "taxa_aprovacao": taxa_aprovacao,
                "pontos_estimados": r[6],
            })

        return {
            "status": "ok",
            "condominio_id": condominio_id,
            "periodo": {"data_inicio": data_inicio, "data_fim": data_fim},
            "torres": torres,
        }

    except Exception as e:
        if conn:
            conn.rollback()
        return {"status": "error", "message": str(e)}

    finally:
        _safe_close(cur, conn)


# Tool: comparar_periodos

class CompararPeriodosArgs(BaseModel):
    condominio_id: int = Field(..., description="ID do condomínio.")
    torre_id: Optional[int] = Field(default=None, description="Filtra por torre.")
    data_inicio: str = Field(..., description="Início do período mais recente (YYYY-MM-DD).")
    data_fim: str = Field(..., description="Fim do período mais recente (YYYY-MM-DD).")
    comparar_periodo_anterior_equivalente: bool = Field(
        default=True,
        description="Se True, calcula automaticamente o período anterior de mesma duração para comparação."
    )
    data_inicio_comparacao: Optional[str] = Field(default=None, description="Início do período de comparação (se não automático).")
    data_fim_comparacao: Optional[str] = Field(default=None, description="Fim do período de comparação (se não automático).")


@tool("comparar_periodos", args_schema=CompararPeriodosArgs)
def comparar_periodos(
    condominio_id: int,
    data_inicio: str,
    data_fim: str,
    torre_id: Optional[int] = None,
    comparar_periodo_anterior_equivalente: bool = True,
    data_inicio_comparacao: Optional[str] = None,
    data_fim_comparacao: Optional[str] = None,
) -> dict:
    """Compara métricas de reciclagem (postagens, aprovação, pontos, participação) entre dois períodos, ex.: mês atual vs. mês anterior."""
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()

        if comparar_periodo_anterior_equivalente and not (data_inicio_comparacao and data_fim_comparacao):
            d_ini = date.fromisoformat(data_inicio)
            d_fim = date.fromisoformat(data_fim)
            duracao_dias = (d_fim - d_ini).days + 1
            data_fim_comparacao = (d_ini - timedelta(days=1)).isoformat()
            data_inicio_comparacao = (d_ini - timedelta(days=duracao_dias)).isoformat()

        if not (data_inicio_comparacao and data_fim_comparacao):
            return {"status": "error", "message": "Informe data_inicio_comparacao e data_fim_comparacao, ou use comparar_periodo_anterior_equivalente=True."}

        def _metrics(d_ini: str, d_fim: str) -> dict:
            params: List[object] = [condominio_id, d_ini, d_fim]
            torre_frag = ""
            if torre_id:
                torre_frag = " AND p.torre_id = %s"
                params.append(torre_id)
            cur.execute(
                f"""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE sv.nome_status = 'aprovada') AS aprovadas,
                    COALESCE(SUM(cr.pontos_base) FILTER (WHERE sv.nome_status = 'aprovada'), 0) AS pontos,
                    COUNT(DISTINCT p.usuario_id) AS participantes
                FROM tb_postagens p
                JOIN tb_lkp_status_validacoes_postagens sv ON sv.id_status_validacao = p.status_validacao_id
                JOIN tb_lkp_categorias_residuos cr ON cr.id_categoria = p.categoria_id
                WHERE p.condominio_id = %s
                  AND {POSTAGEM_BUSINESS_DATE_SQL} >= %s::date
                  AND {POSTAGEM_BUSINESS_DATE_SQL} <= %s::date
                  {torre_frag};
                """,
                tuple(params)
            )
            row = cur.fetchone()
            return {
                "total_postagens": row[0],
                "aprovadas": row[1],
                "pontos_estimados": row[2],
                "moradores_participantes": row[3],
            }

        metrica_atual = _metrics(data_inicio, data_fim)
        metrica_anterior = _metrics(data_inicio_comparacao, data_fim_comparacao)

        def _crescimento(atual, anterior):
            if not anterior:
                return None
            return round(((atual - anterior) / anterior) * 100, 2)

        return {
            "status": "ok",
            "condominio_id": condominio_id,
            "torre_id": torre_id,
            "periodo_atual": {"data_inicio": data_inicio, "data_fim": data_fim, **metrica_atual},
            "periodo_comparacao": {"data_inicio": data_inicio_comparacao, "data_fim": data_fim_comparacao, **metrica_anterior},
            "crescimento_percentual": {
                "postagens": _crescimento(metrica_atual["total_postagens"], metrica_anterior["total_postagens"]),
                "pontos": _crescimento(metrica_atual["pontos_estimados"], metrica_anterior["pontos_estimados"]),
                "participantes": _crescimento(metrica_atual["moradores_participantes"], metrica_anterior["moradores_participantes"]),
            },
        }

    except Exception as e:
        if conn:
            conn.rollback()
        return {"status": "error", "message": str(e)}

    finally:
        _safe_close(cur, conn)


# Tool: taxa_aprovacao_postagens

class TaxaAprovacaoArgs(BaseModel):
    condominio_id: Optional[int] = Field(default=None, description="Filtra por condomínio.")
    torre_id: Optional[int] = Field(default=None, description="Filtra por torre.")
    categoria_id: Optional[int] = Field(default=None, description="Filtra por categoria (id).")
    categoria_nome: Optional[str] = Field(default=None, description="Filtra por categoria (nome, ex.: plástico).")
    data_inicio: Optional[str] = Field(default=None, description="Data inicial local (YYYY-MM-DD).")
    data_fim: Optional[str] = Field(default=None, description="Data final local (YYYY-MM-DD).")
    periodo: Optional[str] = Field(default=None, description="Atalho: hoje | semana | mes | mes_anterior | ano.")
    agrupar_por: str = Field(default="categoria", description="Como agrupar o resultado: 'categoria' ou 'torre'.")


@tool("taxa_aprovacao_postagens", args_schema=TaxaAprovacaoArgs)
def taxa_aprovacao_postagens(
    condominio_id: Optional[int] = None,
    torre_id: Optional[int] = None,
    categoria_id: Optional[int] = None,
    categoria_nome: Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    periodo: Optional[str] = None,
    agrupar_por: str = "categoria",
) -> dict:
    """Mostra a taxa de aprovação/análise/reprovação das postagens, agrupada por categoria de resíduo ou por torre."""
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()

        data_inicio, data_fim = _resolve_periodo(periodo, data_inicio, data_fim)
        resolved_categoria_id = _resolve_categoria_id(cur, categoria_id, categoria_nome)
        if categoria_nome and not categoria_id and not resolved_categoria_id:
            return {"status": "error", "message": f"Categoria '{categoria_nome}' não encontrada."}

        modo = _normalize_text(agrupar_por)
        if modo == "torre":
            group_id_field = "t.id_torre"
            group_name_field = "t.nome_torre"
            join_extra = "LEFT JOIN tb_torres t ON t.id_torre = p.torre_id"
        else:
            group_id_field = "cr.id_categoria"
            group_name_field = "cr.nome_categoria"
            join_extra = ""

        sql = f"""
            SELECT
                {group_id_field} AS grupo_id,
                {group_name_field} AS grupo_nome,
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE sv.nome_status = 'aprovada') AS aprovadas,
                COUNT(*) FILTER (WHERE sv.nome_status = 'em_analise') AS em_analise,
                COUNT(*) FILTER (WHERE sv.nome_status = 'reprovada') AS reprovadas
            FROM tb_postagens p
            JOIN tb_lkp_categorias_residuos cr ON cr.id_categoria = p.categoria_id
            JOIN tb_lkp_status_validacoes_postagens sv ON sv.id_status_validacao = p.status_validacao_id
            {join_extra}
            WHERE 1=1
        """
        params: List[object] = []

        if condominio_id:
            sql += " AND p.condominio_id = %s"
            params.append(condominio_id)

        if torre_id:
            sql += " AND p.torre_id = %s"
            params.append(torre_id)

        if resolved_categoria_id:
            sql += " AND p.categoria_id = %s"
            params.append(resolved_categoria_id)

        sql += _date_filter_fragment(POSTAGEM_BUSINESS_DATE_SQL, data_inicio, data_fim, params)

        sql += f" GROUP BY {group_id_field}, {group_name_field} ORDER BY total DESC;"

        cur.execute(sql, tuple(params))
        rows = cur.fetchall()

        grupos = []
        for r in rows:
            total = r[2]
            aprovadas = r[3]
            grupos.append({
                "grupo_id": r[0],
                "grupo_nome": r[1],
                "total": total,
                "aprovadas": aprovadas,
                "em_analise": r[4],
                "reprovadas": r[5],
                "taxa_aprovacao": round((aprovadas / total * 100), 2) if total else 0.0,
            })

        return {
            "status": "ok",
            "agrupado_por": "torre" if modo == "torre" else "categoria",
            "periodo": {"data_inicio": data_inicio, "data_fim": data_fim},
            "grupos": grupos,
        }

    except Exception as e:
        if conn:
            conn.rollback()
        return {"status": "error", "message": str(e)}

    finally:
        _safe_close(cur, conn)


# Tool: evolucao_reciclagem_periodo


class EvolucaoReciclagemArgs(BaseModel):
    condominio_id: int = Field(..., description="ID do condomínio.")
    torre_id: Optional[int] = Field(default=None, description="Filtra por torre.")
    data_inicio: Optional[str] = Field(default=None, description="Data inicial local (YYYY-MM-DD). Padrão: 30 dias atrás.")
    data_fim: Optional[str] = Field(default=None, description="Data final local (YYYY-MM-DD). Padrão: hoje.")
    granularidade: str = Field(default="dia", description="Agrupamento da série: 'dia', 'semana' ou 'mes'.")


@tool("evolucao_reciclagem_periodo", args_schema=EvolucaoReciclagemArgs)
def evolucao_reciclagem_periodo(
    condominio_id: int,
    torre_id: Optional[int] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    granularidade: str = "dia",
) -> dict:
    """Série temporal de postagens e pontos estimados de um condomínio (ou torre), agregada por dia, semana ou mês — útil para gráficos de evolução."""
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()

        if not data_inicio or not data_fim:
            hoje = datetime.now(SP_TZ).date()
            data_fim = data_fim or hoje.isoformat()
            data_inicio = data_inicio or (hoje - timedelta(days=29)).isoformat()

        trunc_map = {"dia": "day", "semana": "week", "mes": "month"}
        trunc_unit = trunc_map.get(_normalize_text(granularidade), "day")

        params: List[object] = [condominio_id, data_inicio, data_fim]
        torre_frag = ""
        if torre_id:
            torre_frag = " AND p.torre_id = %s"
            params.append(torre_id)

        sql = f"""
            SELECT
                date_trunc('{trunc_unit}', p.data_postagem AT TIME ZONE 'America/Sao_Paulo')::date AS periodo,
                COUNT(*) AS total_postagens,
                COUNT(*) FILTER (WHERE sv.nome_status = 'aprovada') AS aprovadas,
                COALESCE(SUM(cr.pontos_base) FILTER (WHERE sv.nome_status = 'aprovada'), 0) AS pontos_estimados
            FROM tb_postagens p
            JOIN tb_lkp_status_validacoes_postagens sv ON sv.id_status_validacao = p.status_validacao_id
            JOIN tb_lkp_categorias_residuos cr ON cr.id_categoria = p.categoria_id
            WHERE p.condominio_id = %s
              AND {POSTAGEM_BUSINESS_DATE_SQL} >= %s::date
              AND {POSTAGEM_BUSINESS_DATE_SQL} <= %s::date
              {torre_frag}
            GROUP BY periodo
            ORDER BY periodo ASC;
        """

        cur.execute(sql, tuple(params))
        rows = cur.fetchall()

        serie = [
            {
                "periodo": str(r[0]),
                "total_postagens": r[1],
                "aprovadas": r[2],
                "pontos_estimados": r[3],
            }
            for r in rows
        ]

        return {
            "status": "ok",
            "condominio_id": condominio_id,
            "torre_id": torre_id,
            "granularidade": granularidade,
            "periodo": {"data_inicio": data_inicio, "data_fim": data_fim},
            "serie": serie,
        }

    except Exception as e:
        if conn:
            conn.rollback()
        return {"status": "error", "message": str(e)}

    finally:
        _safe_close(cur, conn)


# Tool: resumo_confianca_usuario

class ResumoConfiancaArgs(BaseModel):
    usuario_id: int = Field(..., description="ID do usuário.")

@tool("resumo_confianca_usuario", args_schema=ResumoConfiancaArgs)
def resumo_confianca_usuario(usuario_id: int) -> dict:
    """Retorna o nível de confiança, trust score e histórico de validações/denúncias de um usuário dentro do seu condomínio."""
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT
                nc.nome_nivel, nc.peso_voto,
                uc.trust_score, uc.postagens_validadas_sem_contestacao,
                uc.denuncias_realizadas, uc.denuncias_procedentes, uc.taxa_acerto_denuncias,
                uc.condominio_id, uc.data_entrada
            FROM tb_rel_usuarios_condominios uc
            JOIN tb_lkp_niveis_confianca nc ON nc.id_nivel_confianca = uc.nivel_confianca_id
            WHERE uc.usuario_id = %s AND uc.aprovado = true
            ORDER BY uc.data_entrada DESC
            LIMIT 1;
            """,
            (usuario_id,)
        )
        row = cur.fetchone()
        if not row:
            return {"status": "error", "message": "Nenhum vínculo aprovado encontrado para este usuário."}

        return {
            "status": "ok",
            "usuario_id": usuario_id,
            "condominio_id": row[7],
            "nivel_confianca": row[0],
            "peso_voto": row[1],
            "trust_score": float(row[2]),
            "postagens_validadas_sem_contestacao": row[3],
            "denuncias_realizadas": row[4],
            "denuncias_procedentes": row[5],
            "taxa_acerto_denuncias": float(row[6]) if row[6] is not None else None,
            "membro_desde": str(row[8]),
        }

    except Exception as e:
        if conn:
            conn.rollback()
        return {"status": "error", "message": str(e)}

    finally:
        _safe_close(cur, conn)



# Tool: desempenho_quizzes_condominio
class DesempenhoQuizzesArgs(BaseModel):
    condominio_id: int = Field(..., description="ID do condomínio.")
    torre_id: Optional[int] = Field(default=None, description="Filtra por torre.")
    data_inicio: Optional[str] = Field(default=None, description="Data inicial local (YYYY-MM-DD), por data de conclusão.")
    data_fim: Optional[str] = Field(default=None, description="Data final local (YYYY-MM-DD), por data de conclusão.")
    periodo: Optional[str] = Field(default=None, description="Atalho: hoje | semana | mes | mes_anterior | ano.")

@tool("desempenho_quizzes_condominio", args_schema=DesempenhoQuizzesArgs)
def desempenho_quizzes_condominio(
    condominio_id: int,
    torre_id: Optional[int] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    periodo: Optional[str] = None,
) -> dict:
    """Resumo do engajamento educacional (quizzes) de um condomínio: tentativas concluídas, taxa de aprovação e pontos de recompensa distribuídos."""
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()

        data_inicio, data_fim = _resolve_periodo(periodo, data_inicio, data_fim)

        params: List[object] = [condominio_id]
        frag = _date_filter_fragment(
            "(tq.concluido_em AT TIME ZONE 'America/Sao_Paulo')::date", data_inicio, data_fim, params
        )
        torre_frag = ""
        if torre_id:
            torre_frag = " AND tq.torre_id = %s"
            params.append(torre_id)

        cur.execute(
            f"""
            SELECT
                COUNT(*) AS total_tentativas,
                COUNT(*) FILTER (WHERE tq.aprovado = true) AS aprovadas,
                COUNT(DISTINCT tq.usuario_id) AS moradores_participantes,
                COALESCE(SUM(q.pontos_recompensa) FILTER (WHERE tq.aprovado = true), 0) AS pontos_distribuidos
            FROM tb_tentativas_quiz tq
            JOIN tb_quizzes q ON q.id_quiz = tq.quiz_id
            WHERE tq.condominio_id = %s
              AND tq.concluido_em IS NOT NULL
              {frag}
              {torre_frag};
            """,
            tuple(params)
        )
        row = cur.fetchone()
        total = row[0]
        aprovadas = row[1]

        return {
            "status": "ok",
            "condominio_id": condominio_id,
            "torre_id": torre_id,
            "periodo": {"data_inicio": data_inicio, "data_fim": data_fim},
            "total_tentativas_concluidas": total,
            "aprovadas": aprovadas,
            "taxa_aprovacao": round((aprovadas / total * 100), 2) if total else 0.0,
            "moradores_participantes": row[2],
            "pontos_distribuidos": row[3],
        }

    except Exception as e:
        if conn:
            conn.rollback()
        return {"status": "error", "message": str(e)}

    finally:
        _safe_close(cur, conn)



# Tool: listar_postagens (consulta livre, catch-all)
class ListarPostagensArgs(BaseModel):
    usuario_id: Optional[int] = Field(default=None, description="Filtra por usuário.")
    condominio_id: Optional[int] = Field(default=None, description="Filtra por condomínio.")
    torre_id: Optional[int] = Field(default=None, description="Filtra por torre.")
    categoria_id: Optional[int] = Field(default=None, description="Filtra por categoria (id).")
    categoria_nome: Optional[str] = Field(default=None, description="Filtra por categoria (nome).")
    status_nome: Optional[str] = Field(default=None, description="Filtra por status: aprovada | em_analise | reprovada.")
    data_inicio: Optional[str] = Field(default=None, description="Data inicial local (YYYY-MM-DD).")
    data_fim: Optional[str] = Field(default=None, description="Data final local (YYYY-MM-DD).")
    periodo: Optional[str] = Field(default=None, description="Atalho: hoje | semana | mes | mes_anterior | ano.")
    limite: int = Field(default=50, description="Máximo de registros retornados.")


@tool("listar_postagens", args_schema=ListarPostagensArgs)
def listar_postagens(
    usuario_id: Optional[int] = None,
    condominio_id: Optional[int] = None,
    torre_id: Optional[int] = None,
    categoria_id: Optional[int] = None,
    categoria_nome: Optional[str] = None,
    status_nome: Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    periodo: Optional[str] = None,
    limite: int = 50,
) -> dict:
    """Lista postagens de reciclagem com filtros livres (usuário, condomínio, torre, categoria, status, período). Use para perguntas analíticas específicas não cobertas pelas outras ferramentas."""
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()

        data_inicio, data_fim = _resolve_periodo(periodo, data_inicio, data_fim)

        resolved_categoria_id = _resolve_categoria_id(cur, categoria_id, categoria_nome)
        if categoria_nome and not categoria_id and not resolved_categoria_id:
            return {"status": "error", "message": f"Categoria '{categoria_nome}' não encontrada."}

        resolved_status_id = _resolve_status_validacao_id(cur, status_nome)
        if status_nome and not resolved_status_id:
            return {"status": "error", "message": f"Status '{status_nome}' não reconhecido. Use aprovada, em_analise ou reprovada."}

        sql = """
            SELECT
                p.id_postagem, p.usuario_id, u.nome_usuario, p.condominio_id,
                p.torre_id, t.nome_torre, cr.nome_categoria, cr.pontos_base,
                sv.nome_status, p.saldo_confianca, p.data_postagem
            FROM tb_postagens p
            JOIN tb_usuarios u ON u.id_usuario = p.usuario_id
            JOIN tb_lkp_categorias_residuos cr ON cr.id_categoria = p.categoria_id
            JOIN tb_lkp_status_validacoes_postagens sv ON sv.id_status_validacao = p.status_validacao_id
            LEFT JOIN tb_torres t ON t.id_torre = p.torre_id
            WHERE 1=1
        """
        params: List[object] = []

        if usuario_id:
            sql += " AND p.usuario_id = %s"
            params.append(usuario_id)

        if condominio_id:
            sql += " AND p.condominio_id = %s"
            params.append(condominio_id)

        if torre_id:
            sql += " AND p.torre_id = %s"
            params.append(torre_id)

        if resolved_categoria_id:
            sql += " AND p.categoria_id = %s"
            params.append(resolved_categoria_id)

        if resolved_status_id:
            sql += " AND p.status_validacao_id = %s"
            params.append(resolved_status_id)

        sql += _date_filter_fragment(POSTAGEM_BUSINESS_DATE_SQL, data_inicio, data_fim, params)

        sql += " ORDER BY p.data_postagem DESC LIMIT %s"
        params.append(limite)

        cur.execute(sql, tuple(params))
        rows = cur.fetchall()

        items = [
            {
                "id_postagem": r[0],
                "usuario_id": r[1],
                "usuario_nome": r[2],
                "condominio_id": r[3],
                "torre_id": r[4],
                "torre_nome": r[5],
                "categoria_nome": r[6],
                "pontos_base": r[7],
                "status": r[8],
                "saldo_confianca": r[9],
                "data_postagem": str(r[10]),
            }
            for r in rows
        ]

        return {
            "status": "ok",
            "count": len(items),
            "periodo": {"data_inicio": data_inicio, "data_fim": data_fim},
            "items": items,
        }

    except Exception as e:
        if conn:
            conn.rollback()
        return {"status": "error", "message": str(e)}

    finally:
        _safe_close(cur, conn)


# ============================================================================
# Tools de SIMULAÇÃO "E SE" (chamam fn_ritmo_diario_torres / fn_projecao_reciclagem
# — ver ecociente_simulacao_e_se.sql. A regra de projeção fica no banco.)
# ============================================================================

# Tool: ritmo_diario_torres

class RitmoDiarioTorresArgs(BaseModel):
    condominio_id: int = Field(..., description="ID do condomínio.")
    dias_baseline: int = Field(default=30, description="Janela (em dias) usada para calcular o ritmo diário atual de cada torre.")


@tool("ritmo_diario_torres", args_schema=RitmoDiarioTorresArgs)
def ritmo_diario_torres(condominio_id: int, dias_baseline: int = 30) -> dict:
    """
    Ritmo diário (pontos/dia) de cada torre de um condomínio, na janela de
    baseline informada, e o quanto cada torre está atrás da torre líder em
    percentual. Use para responder "qual torre está reciclando melhor?" ou
    como insumo para simular_torre_no_ritmo_da_lider.
    """
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()

        cur.execute(
            "SELECT * FROM fn_ritmo_diario_torres(%s, %s);",
            (condominio_id, dias_baseline)
        )
        rows = cur.fetchall()

        torres = [
            {
                "torre_id": r[0],
                "torre_nome": r[1],
                "pontos_baseline": r[2],
                "media_diaria": float(r[3]) if r[3] is not None else 0.0,
                "percentual_vs_lider": float(r[4]) if r[4] is not None else None,
            }
            for r in rows
        ]

        return {
            "status": "ok",
            "condominio_id": condominio_id,
            "dias_baseline": dias_baseline,
            "torres": torres,
        }

    except Exception as e:
        if conn:
            conn.rollback()
        return {"status": "error", "message": str(e)}

    finally:
        _safe_close(cur, conn)


# Tool: simular_projecao_reciclagem (motor genérico da simulação "e se")

class SimularProjecaoArgs(BaseModel):
    condominio_id: int = Field(..., description="ID do condomínio.")
    torre_id: Optional[int] = Field(default=None, description="Se informado, simula apenas essa torre. Se omitido, simula o condomínio inteiro.")
    dias_baseline: int = Field(default=30, description="Janela (em dias) usada para calcular o ritmo diário atual.")
    percentual_incremento: float = Field(default=0.0, description="Incremento percentual simulado sobre o ritmo atual. Ex.: 15 = 'e se reciclasse 15% a mais'.")
    meta_pontos: Optional[int] = Field(default=None, description="Meta absoluta de pontos acumulados. Se informado, retorna dias necessários (atual vs. simulado) para atingi-la.")
    horizonte_dias: int = Field(default=30, description="Nº de dias futuros para projetar o total de pontos (atual vs. simulado), independente de haver meta.")


@tool("simular_projecao_reciclagem", args_schema=SimularProjecaoArgs)
def simular_projecao_reciclagem(
    condominio_id: int,
    torre_id: Optional[int] = None,
    dias_baseline: int = 30,
    percentual_incremento: float = 0.0,
    meta_pontos: Optional[int] = None,
    horizonte_dias: int = 30,
) -> dict:
    """
    Simulação "e se": projeta pontos futuros e/ou dias necessários para
    atingir uma meta, comparando o ritmo diário atual (média de pontos
    aprovados/dia na janela de baseline) com um ritmo simulado
    (+percentual_incremento). Escopo: condomínio inteiro ou uma torre
    específica. Exemplos de uso: "se o condomínio reciclasse 15% a mais,
    quando bateria 5000 pontos?", "quantos pontos teríamos em 60 dias no
    ritmo atual?".
    """
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()

        cur.execute(
            "SELECT * FROM fn_projecao_reciclagem(%s, %s, %s, %s, %s, %s);",
            (condominio_id, torre_id, dias_baseline, percentual_incremento, meta_pontos, horizonte_dias)
        )
        row = cur.fetchone()
        if not row:
            return {"status": "error", "message": "Não foi possível calcular a projeção para os parâmetros informados."}

        return {
            "status": "ok",
            "condominio_id": condominio_id,
            "torre_id": torre_id,
            "pontos_acumulados_total": row[0],
            "dias_baseline_utilizados": row[1],
            "media_diaria_atual": float(row[2]) if row[2] is not None else 0.0,
            "media_diaria_simulada": float(row[3]) if row[3] is not None else 0.0,
            "percentual_incremento_aplicado": float(row[4]),
            "meta_pontos": row[5],
            "dias_para_meta_atual": row[6],
            "dias_para_meta_simulado": row[7],
            "horizonte_dias": row[8],
            "projecao_pontos_atual": row[9],
            "projecao_pontos_simulado": row[10],
        }

    except Exception as e:
        if conn:
            conn.rollback()
        return {"status": "error", "message": str(e)}

    finally:
        _safe_close(cur, conn)


# Tool: simular_torre_no_ritmo_da_lider (composição: benchmark + projeção)

class SimularTorreLiderancaArgs(BaseModel):
    condominio_id: int = Field(..., description="ID do condomínio.")
    torre_id: int = Field(..., description="Torre a simular no ritmo da torre líder do condomínio.")
    dias_baseline: int = Field(default=30, description="Janela (em dias) usada para calcular os ritmos atuais.")
    meta_pontos: Optional[int] = Field(default=None, description="Meta absoluta de pontos acumulados da torre. Se informado, retorna dias necessários (ritmo atual vs. ritmo da líder) para atingi-la.")
    horizonte_dias: int = Field(default=30, description="Nº de dias futuros para projetar o total de pontos da torre (ritmo atual vs. ritmo da líder).")


@tool("simular_torre_no_ritmo_da_lider", args_schema=SimularTorreLiderancaArgs)
def simular_torre_no_ritmo_da_lider(
    condominio_id: int,
    torre_id: int,
    dias_baseline: int = 30,
    meta_pontos: Optional[int] = None,
    horizonte_dias: int = 30,
) -> dict:
    """
    Simulação "e se a torre X reciclasse no ritmo da torre líder do
    condomínio?". Primeiro calcula o quanto a torre está atrás da líder
    (fn_ritmo_diario_torres) e usa esse gap percentual como incremento na
    projeção (fn_projecao_reciclagem). Se a torre já for a líder, o gap é
    0% e a projeção reflete apenas o ritmo atual dela.
    """
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()

        cur.execute(
            "SELECT * FROM fn_ritmo_diario_torres(%s, %s);",
            (condominio_id, dias_baseline)
        )
        rows = cur.fetchall()
        alvo = next((r for r in rows if r[0] == torre_id), None)
        if not alvo:
            return {"status": "error", "message": f"Torre {torre_id} não encontrada no condomínio {condominio_id}."}

        gap_percentual = float(alvo[4]) if alvo[4] is not None else 0.0

        cur.execute(
            "SELECT * FROM fn_projecao_reciclagem(%s, %s, %s, %s, %s, %s);",
            (condominio_id, torre_id, dias_baseline, gap_percentual, meta_pontos, horizonte_dias))
        proj = cur.fetchone()
        if not proj:
            return {"status": "error", "message": "Não foi possível calcular a projeção para os parâmetros informados."}

        return {
            "status": "ok",
            "condominio_id": condominio_id,
            "torre_id": torre_id,
            "torre_nome": alvo[1],
            "gap_percentual_vs_lider": gap_percentual,
            "pontos_acumulados_total": proj[0],
            "dias_baseline_utilizados": proj[1],
            "media_diaria_atual": float(proj[2]) if proj[2] is not None else 0.0,
            "media_diaria_no_ritmo_da_lider": float(proj[3]) if proj[3] is not None else 0.0,
            "meta_pontos": proj[5],
            "dias_para_meta_ritmo_atual": proj[6],
            "dias_para_meta_ritmo_lider": proj[7],
            "horizonte_dias": proj[8],
            "projecao_pontos_ritmo_atual": proj[9],
            "projecao_pontos_ritmo_lider": proj[10],
        }

    except Exception as e:
        if conn:
            conn.rollback()
        return {"status": "error", "message": str(e)}

    finally:
        _safe_close(cur, conn)


TOOLS = [material_mais_reciclado,
    resumo_reciclagem_condominio,
    resumo_reciclagem_morador,
    comparar_torres,
    comparar_periodos,
    taxa_aprovacao_postagens,
    evolucao_reciclagem_periodo,
    resumo_confianca_usuario,
    desempenho_quizzes_condominio,
    listar_postagens,
    ritmo_diario_torres,
    simular_projecao_reciclagem,
    simular_torre_no_ritmo_da_lider,
]