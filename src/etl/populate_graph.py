"""Pipeline de povoamento do grafo Neo4j a partir do PostgreSQL.

Segue a mesma convenção do resto do projeto: lê dados via `PostgresDatabase`
(pool `psycopg2`, somente leitura) e escreve no Neo4j via `Neo4jDatabase`
(`neo4j_db`, driver assíncrono já usado pelas tools do agente `grafo`).

Modelo de grafo populado (nós e relacionamentos derivados 1:1 de tabelas/
colunas existentes no schema `sql/ecociente_schema.sql`):

Nós
    Usuario(id, nome, perfil)
    Condominio(id, nome)
    Torre(id, nome)
    Cooperativa(id, nome)
    CategoriaResiduo(id, nome)
    Postagem(id, capturada_em, status_validacao)

Relacionamentos
    (Usuario)-[:MORA_EM]->(Condominio)                  <- tb_moradores
    (Usuario)-[:PERTENCE_A {trust_score, ...}]->(Condominio)
                                                          <- tb_rel_usuarios_condominios
    (Torre)-[:PERTENCE_A]->(Condominio)                  <- tb_torres.condominio_id
    (Cooperativa)-[:REPRESENTADA_POR]->(Usuario)         <- tb_cooperativas.usuario_id
    (Usuario)-[:CRIOU]->(Postagem)                       <- tb_postagens.usuario_id
    (Postagem)-[:NO_CONDOMINIO]->(Condominio)            <- tb_postagens.condominio_id
    (Postagem)-[:NA_TORRE]->(Torre)                      <- tb_postagens.torre_id
    (Postagem)-[:DA_CATEGORIA]->(CategoriaResiduo)       <- tb_postagens.categoria_id
    (Usuario)-[:VALIDOU {peso, tipo}]->(Postagem)        <- tb_rel_votos_postagens (sem motivo_denuncia_id)
    (Usuario)-[:DENUNCIOU {motivo, peso}]->(Postagem)    <- tb_rel_votos_postagens (com motivo_denuncia_id)

Fora do escopo desta primeira versão (motivo: não há tabela/coluna que
represente diretamente a relação — dependem de lógica de negócio/analítica
ainda não definida, e não seriam uma extração ETL 1:1):
    TEM_DIFICULDADE_EM   (usuário x categoria, a partir de notas de quiz)
    RECOMENDADO_PARA     (conteúdo recomendado por usuário)
Essas duas ficam registradas no README como pendências explícitas, em vez
de serem populadas com heurísticas arbitrárias.

Idempotência: todo relacionamento e nó é escrito com `MERGE` (nunca
`CREATE`), então rodar o pipeline várias vezes sobre a mesma base
Postgres não duplica nós nem arestas — ao contrário do comportamento
conhecido do repositório irmão `md-rpa-integration`.

Uso:
    python -m src.etl.populate_graph
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.core.config import settings
from src.database.neo4j import neo4j_db
from src.database.postgres import postgres_db

logger = logging.getLogger("ecociente.etl.populate_graph")

_BATCH_SIZE = 500


class GraphPopulator:
    """Extrai do PostgreSQL e faz MERGE idempotente no Neo4j, nó a nó,
    relação a relação, em lotes (`UNWIND $rows AS row`)."""

    def __init__(self) -> None:
        self.postgres = postgres_db
        self.neo4j = neo4j_db

    async def run(self) -> dict[str, int]:
        stats: dict[str, int] = {}

        stats["usuarios"] = await self._load_usuarios()
        stats["condominios"] = await self._load_condominios()
        stats["torres"] = await self._load_torres()
        stats["cooperativas"] = await self._load_cooperativas()
        stats["categorias_residuo"] = await self._load_categorias()
        stats["postagens"] = await self._load_postagens()

        stats["mora_em"] = await self._load_mora_em()
        stats["pertence_a_usuario_condominio"] = await self._load_pertence_a()
        stats["torre_pertence_a_condominio"] = await self._load_torre_pertence_a()
        stats["cooperativa_representada_por"] = await self._load_cooperativa_representada_por()
        stats["criou"] = await self._load_criou()
        stats["postagem_no_condominio"] = await self._load_postagem_no_condominio()
        stats["postagem_na_torre"] = await self._load_postagem_na_torre()
        stats["postagem_da_categoria"] = await self._load_postagem_da_categoria()
        stats["validou"] = await self._load_validou()
        stats["denunciou"] = await self._load_denunciou()

        logger.info("etl_grafo_concluido", extra={"stats": stats})
        return stats

    # ------------------------------------------------------------------ #
    # Nós
    # ------------------------------------------------------------------ #

    async def _load_usuarios(self) -> int:
        rows = self.postgres.fetch_all(
            """
            SELECT u.id_usuario AS id, u.nome_usuario AS nome, t.nome_tipo AS perfil
            FROM tb_usuarios u
            JOIN tb_lkp_tipos_usuarios t ON t.id_tipo_usuario = u.tipo_usuario_id
            """
        )
        return await self._merge_nodes(
            "Usuario",
            rows,
            "MERGE (n:Usuario {id: row.id}) SET n.nome = row.nome, n.perfil = row.perfil",
        )

    async def _load_condominios(self) -> int:
        rows = self.postgres.fetch_all(
            "SELECT id_condominio AS id, nome_condominio AS nome FROM tb_condominios"
        )
        return await self._merge_nodes(
            "Condominio",
            rows,
            "MERGE (n:Condominio {id: row.id}) SET n.nome = row.nome",
        )

    async def _load_torres(self) -> int:
        rows = self.postgres.fetch_all(
            "SELECT id_torre AS id, nome_torre AS nome FROM tb_torres"
        )
        return await self._merge_nodes(
            "Torre",
            rows,
            "MERGE (n:Torre {id: row.id}) SET n.nome = row.nome",
        )

    async def _load_cooperativas(self) -> int:
        rows = self.postgres.fetch_all(
            "SELECT id_cooperativa AS id, nome_cooperativa AS nome FROM tb_cooperativas"
        )
        return await self._merge_nodes(
            "Cooperativa",
            rows,
            "MERGE (n:Cooperativa {id: row.id}) SET n.nome = row.nome",
        )

    async def _load_categorias(self) -> int:
        rows = self.postgres.fetch_all(
            "SELECT id_categoria AS id, nome_categoria AS nome FROM tb_lkp_categorias_residuos"
        )
        return await self._merge_nodes(
            "CategoriaResiduo",
            rows,
            "MERGE (n:CategoriaResiduo {id: row.id}) SET n.nome = row.nome",
        )

    async def _load_postagens(self) -> int:
        rows = self.postgres.fetch_all(
            """
            SELECT
                p.id_postagem AS id,
                p.capturada_em AS capturada_em,
                s.nome_status AS status_validacao
            FROM tb_postagens p
            JOIN tb_lkp_status_validacoes_postagens s
                ON s.id_status_validacao = p.status_validacao_id
            """
        )
        for row in rows:
            if row.get("capturada_em") is not None:
                row["capturada_em"] = row["capturada_em"].isoformat()
        return await self._merge_nodes(
            "Postagem",
            rows,
            "MERGE (n:Postagem {id: row.id}) "
            "SET n.capturada_em = row.capturada_em, n.status_validacao = row.status_validacao",
        )

    # ------------------------------------------------------------------ #
    # Relacionamentos
    # ------------------------------------------------------------------ #

    async def _load_mora_em(self) -> int:
        rows = self.postgres.fetch_all(
            "SELECT usuario_id, condominio_id FROM tb_moradores"
        )
        return await self._merge_edges(
            rows,
            "MATCH (u:Usuario {id: row.usuario_id}), (c:Condominio {id: row.condominio_id}) "
            "MERGE (u)-[:MORA_EM]->(c)",
        )

    async def _load_pertence_a(self) -> int:
        rows = self.postgres.fetch_all(
            """
            SELECT
                usuario_id,
                condominio_id,
                trust_score,
                postagens_validadas_sem_contestacao,
                denuncias_realizadas,
                denuncias_procedentes
            FROM tb_rel_usuarios_condominios
            """
        )
        for row in rows:
            if row.get("trust_score") is not None:
                row["trust_score"] = float(row["trust_score"])
        return await self._merge_edges(
            rows,
            "MATCH (u:Usuario {id: row.usuario_id}), (c:Condominio {id: row.condominio_id}) "
            "MERGE (u)-[r:PERTENCE_A]->(c) "
            "SET r.trust_score = row.trust_score, "
            "    r.postagens_validadas_sem_contestacao = row.postagens_validadas_sem_contestacao, "
            "    r.denuncias_realizadas = row.denuncias_realizadas, "
            "    r.denuncias_procedentes = row.denuncias_procedentes",
        )

    async def _load_torre_pertence_a(self) -> int:
        rows = self.postgres.fetch_all(
            "SELECT id_torre AS torre_id, condominio_id FROM tb_torres"
        )
        return await self._merge_edges(
            rows,
            "MATCH (t:Torre {id: row.torre_id}), (c:Condominio {id: row.condominio_id}) "
            "MERGE (t)-[:PERTENCE_A]->(c)",
        )