from __future__ import annotations

import logging
from typing import Any

from langchain.tools import tool
from neo4j.exceptions import Neo4jError
from pydantic import BaseModel, Field

from src.database.neo4j import neo4j_db
from src.etl.graph_model import NODE_LABELS, RELATIONSHIP_TYPES
from src.security.policies import authorize_action
from src.security.tool_scope import current_scope

logger = logging.getLogger("ecociente.grafo")

# Mesmo vocabulário que o ETL (src/etl/populate_graph.py) realmente escreve.
# Whitelist obrigatória: labels e tipos de relação são interpolados no Cypher.
_LABELS_PERMITIDOS = set(NODE_LABELS)
_RELACOES_PERMITIDAS = set(RELATIONSHIP_TYPES)

_SEM_ESCOPO = {"status": "negado", "message": "Contexto de acesso ausente para consultar o grafo."}


def _escopo_grafo() -> dict | None:
    """Parâmetros do escopo do grafo: condomínio, usuário e se só o próprio Usuario é visível."""
    user = current_scope()
    if user is None or user.condominio_id is None:
        return None
    return {"cid": user.condominio_id, "uid": user.user_id, "so_proprio": not authorize_action(user, "analytics_macro").allowed}


def _no_escopo(var: str, so_proprio: bool = False) -> str:
    """Predicado Cypher: o nó pertence ao condomínio `$cid` ou é entidade global do domínio."""
    # ponytail: EXISTS por nó dentro do shortestPath pode cair em busca exaustiva; ok no volume atual.
    base = (
        f"(({var}:Condominio AND {var}.id = $cid) OR {var}:CategoriaResiduo OR {var}:Curso OR {var}:Cooperativa "
        f"OR EXISTS {{ ({var})-[:PERTENCE_A|MORA_EM|NO_CONDOMINIO]->(:Condominio {{id: $cid}}) }})"
    )
    if not so_proprio:
        return base
    # Sem analytics_macro: outro morador não aparece (nem no meio do caminho).
    return f"({base} AND (NOT {var}:Usuario OR {var}.id = $uid))"


def _grafo_failure(exc: Exception) -> dict[str, str]:
    logger.exception("Falha de consulta ao grafo (Neo4j)", exc_info=exc)
    if isinstance(exc, RuntimeError):
        return {"status": "indisponivel", "message": "Camada de relacionamentos (Neo4j) não está inicializada."}
    if isinstance(exc, Neo4jError):
        return {"status": "erro", "message": "A consulta ao grafo falhou; verifique o schema e a conectividade."}
    raise exc


def _validar_label(label: str) -> str | None:
    return label if label in _LABELS_PERMITIDOS else None


def _validar_relacao(tipo_relacao: str | None) -> str | None:
    if tipo_relacao is None:
        return None
    return tipo_relacao if tipo_relacao in _RELACOES_PERMITIDAS else None


class CaminhoRelacionamentoArgs(BaseModel):
    label_origem: str
    id_origem: int = Field(gt=0)
    label_destino: str
    id_destino: int = Field(gt=0)
    tipo_relacao: str | None = None
    profundidade_maxima: int = Field(default=4, ge=1, le=6)


@tool("caminho_relacionamento", args_schema=CaminhoRelacionamentoArgs)
async def caminho_relacionamento(
    label_origem: str,
    id_origem: int,
    label_destino: str,
    id_destino: int,
    tipo_relacao: str | None = None,
    profundidade_maxima: int = 4,
) -> dict[str, Any]:
    """Encontra o caminho mais curto entre duas entidades já identificadas por label e id."""
    escopo = _escopo_grafo()
    if escopo is None:
        return dict(_SEM_ESCOPO)
    sp = escopo["so_proprio"]
    origem = _validar_label(label_origem)
    destino = _validar_label(label_destino)
    if origem is None or destino is None:
        return {"status": "erro", "message": "Label de entidade não reconhecido pelo domínio do EcoCiente."}
    relacao = _validar_relacao(tipo_relacao)
    if tipo_relacao is not None and relacao is None:
        return {"status": "erro", "message": "Tipo de relação não reconhecido pelo domínio do EcoCiente."}

    rel_pattern = f":{relacao}" if relacao else ""
    query = f"""
    MATCH (origem:{origem} {{id: $id_origem}}), (destino:{destino} {{id: $id_destino}})
    WHERE {_no_escopo("origem", sp)} AND {_no_escopo("destino", sp)}
    MATCH caminho = shortestPath((origem)-[{rel_pattern}*1..{profundidade_maxima}]-(destino))
    WHERE ALL(n IN nodes(caminho) WHERE {_no_escopo("n", sp)})
    RETURN [n IN nodes(caminho) | {{label: labels(n)[0], id: n.id}}] AS nos,
           [r IN relationships(caminho) | type(r)] AS relacoes,
           length(caminho) AS distancia
    LIMIT 1
    """
    try:
        rows = await neo4j_db.execute(
            query,
            {"id_origem": id_origem, "id_destino": id_destino, "cid": escopo["cid"], "uid": escopo["uid"]},
        )
    except (RuntimeError, Neo4jError) as exc:
        return _grafo_failure(exc)

    if not rows:
        return {"status": "sem_caminho", "origem": {origem: id_origem}, "destino": {destino: id_destino}}

    row = rows[0]
    return {
        "status": "ok",
        "distancia": row["distancia"],
        "nos": row["nos"],
        "relacoes": row["relacoes"],
    }


class ConexoesDiretasArgs(BaseModel):
    label: str
    id: int = Field(gt=0)
    tipo_relacao: str | None = None
    profundidade: int = Field(default=1, ge=1, le=3)


@tool("conexoes_diretas", args_schema=ConexoesDiretasArgs)
async def conexoes_diretas(
    label: str,
    id: int,
    tipo_relacao: str | None = None,
    profundidade: int = 1,
) -> dict[str, Any]:
    """Lista as entidades conectadas a uma entidade até a profundidade pedida (máx. 3)."""
    escopo = _escopo_grafo()
    if escopo is None:
        return dict(_SEM_ESCOPO)
    sp = escopo["so_proprio"]
    rotulo = _validar_label(label)
    if rotulo is None:
        return {"status": "erro", "message": "Label de entidade não reconhecido pelo domínio do EcoCiente."}
    relacao = _validar_relacao(tipo_relacao)
    if tipo_relacao is not None and relacao is None:
        return {"status": "erro", "message": "Tipo de relação não reconhecido pelo domínio do EcoCiente."}

    rel_pattern = f":{relacao}" if relacao else ""
    query = f"""
    MATCH (centro:{rotulo} {{id: $id}})
    WHERE {_no_escopo("centro", sp)}
    MATCH p = (centro)-[{rel_pattern}*1..{profundidade}]-(vizinho)
    WHERE ALL(x IN nodes(p) WHERE {_no_escopo("x", sp)})
    RETURN DISTINCT labels(vizinho)[0] AS label_vizinho, vizinho.id AS id_vizinho
    LIMIT 50
    """
    try:
        rows = await neo4j_db.execute(query, {"id": id, "cid": escopo["cid"], "uid": escopo["uid"]})
    except (RuntimeError, Neo4jError) as exc:
        return _grafo_failure(exc)

    return {
        "status": "ok",
        "entidade": {rotulo: id},
        "total_conexoes": len(rows),
        "conexoes": [{"label": r["label_vizinho"], "id": r["id_vizinho"]} for r in rows],
    }


class UsuariosMaisConectadosArgs(BaseModel):
    condominio_id: int = Field(gt=0)
    limite: int = Field(default=10, ge=1, le=50)


@tool("usuarios_mais_conectados", args_schema=UsuariosMaisConectadosArgs)
async def usuarios_mais_conectados(condominio_id: int, limite: int = 10) -> dict[str, Any]:
    """Ranking de moradores do condomínio por grau de conexão (postagens criadas/validadas)."""
    escopo = _escopo_grafo()
    if escopo is None or condominio_id != escopo["cid"]:
        return dict(_SEM_ESCOPO)
    query = """
    MATCH (u:Usuario)-[:PERTENCE_A]->(c:Condominio {id: $condominio_id})
    MATCH (u)-[r:CRIOU|VALIDOU]-(p:Postagem)
    RETURN u.id AS usuario_id, COUNT(DISTINCT p) AS conexoes
    ORDER BY conexoes DESC
    LIMIT $limite
    """
    try:
        rows = await neo4j_db.execute(
            query,
            {"condominio_id": condominio_id, "limite": limite},
        )
    except (RuntimeError, Neo4jError) as exc:
        return _grafo_failure(exc)

    return {
        "status": "ok",
        "condominio_id": condominio_id,
        "top_moradores": [{"usuario_id": r["usuario_id"], "conexoes": r["conexoes"]} for r in rows],
    }


TOOLS = [
    caminho_relacionamento,
    conexoes_diretas,
    usuarios_mais_conectados,
]
