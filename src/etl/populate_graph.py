"""Pipeline de povoamento do grafo Neo4j a partir do PostgreSQL.

Lê via `PostgresDatabase` (pool `psycopg2`, somente leitura) e escreve via
`Neo4jDatabase` (`neo4j_db`, o mesmo driver das tools do agente `grafo`).

Modos
-----
- ``full``: relê todas as tabelas. Recomendado 1x por dia (captura edições de
  cadastro que o Postgres não carimba com data, como nome/perfil de usuário).
- ``incremental``: relê só o que mudou desde o último sucesso, usando as colunas
  de data que o schema já tem (lista abaixo). Sem checkpoint anterior, vira full.

O checkpoint fica no próprio grafo, em ``(:EtlCheckpoint {pipeline})``, gravado
apenas quando a execução termina sem erro. A janela incremental começa em
``último_sucesso - overlap`` (10 min): uma transação que começou antes da
execução anterior mas só confirmou depois ainda é capturada. Como tudo é
``MERGE``, reler a sobreposição não duplica nada.

Nós e relacionamentos
---------------------
============================================  ===============================  ==========================================
Relação                                       Origem                           Incremental por
============================================  ===============================  ==========================================
(Usuario)-[:MORA_EM]->(Condominio)            tb_moradores                     — (sem data; sempre relida)
(Usuario)-[:PERTENCE_A {trust_score…}]->(C)   tb_rel_usuarios_condominios      — (métricas mudam sem data; sempre relida)
(Torre)-[:PERTENCE_A]->(Condominio)           tb_torres.condominio_id          — (dimensão pequena)
(Cooperativa)-[:REPRESENTADA_POR]->(Usuario)  tb_cooperativas.usuario_id       data_cadastro
(Usuario)-[:CRIOU]->(Postagem)                tb_postagens                     data_postagem OU resolvido_em
(Postagem)-[:NO_CONDOMINIO|NA_TORRE|DA_CATEGORIA]  tb_postagens                data_postagem OU resolvido_em
(Usuario)-[:VALIDOU {peso, tipo}]->(Postagem)  tb_rel_votos_postagens (sem motivo)  votado_em
(Usuario)-[:DENUNCIOU {peso, motivo}]->(P)     tb_rel_votos_postagens (com motivo)  votado_em
(Usuario)-[:APROVADO_EM]->(Curso)             tb_tentativas_quiz + tb_quizzes  pares (usuário, curso) com tentativa nova
(Usuario)-[:TEM_DIFICULDADE_EM]->(Curso)      tb_tentativas_quiz + tb_quizzes  pares (usuário, curso) com tentativa nova
(Curso)-[:RECOMENDADO_PARA]->(Usuario)        calculada no próprio grafo       sempre recalculada (derivada)
============================================  ===============================  ==========================================

Nós: Usuario (incremental por ``registro_em``), Condominio, Torre, Cooperativa,
CategoriaResiduo, Curso, Postagem (incremental como as postagens acima).

Regras de negócio das relações derivadas (constantes no topo do módulo)
------------------------------------------------------------------------
- ``TEM_DIFICULDADE_EM``: no curso, o usuário tem pelo menos
  ``DIFICULDADE_MIN_REPROVACOES`` tentativas de quiz concluídas e reprovadas, e
  essas reprovações são pelo menos ``DIFICULDADE_MIN_TAXA`` das tentativas.
  Se o usuário melhora e deixa de cumprir a regra, a aresta é REMOVIDA.
- ``APROVADO_EM``: ao menos uma tentativa aprovada em algum quiz do curso.
- ``RECOMENDADO_PARA`` (curso → usuário), dois motivos:
    * ``reforco``: o usuário tem dificuldade no curso (score = nº de reprovações);
    * ``vizinhos_aprovados``: pelo menos ``RECOMENDACAO_MIN_VIZINHOS`` pessoas do
      mesmo condomínio foram aprovadas no curso e o usuário ainda não
      (score = nº de vizinhos aprovados). Filtragem colaborativa por vizinhança,
      que é exatamente o tipo de pergunta em que o grafo ganha do SQL.
  Só cursos ativos são recomendados. Arestas de execuções anteriores que não
  foram reconfirmadas são removidas no fim (sem janela vazia no meio).

Limitações conhecidas
---------------------
- Exclusões no Postgres não são propagadas (o schema não tem soft delete nem log
  de exclusão); rode ``full`` com ``--prune`` para remover nós órfãos de postagem.
- Duas execuções simultâneas não são bloqueadas aqui: no Kubernetes, os
  CronJobs usam ``concurrencyPolicy: Forbid``; no modo loop há um único processo.

Uso
---
    python -m src.etl.populate_graph --mode full
    python -m src.etl.populate_graph --mode incremental
    python -m src.etl.populate_graph --loop-seconds 900 --full-every-hours 24
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from src.core.config import settings
from src.database.neo4j import neo4j_db
from src.database.postgres import postgres_db
from src.etl.graph_model import NODE_LABELS

logger = logging.getLogger("ecociente.etl.populate_graph")

PIPELINE = "postgres_to_graph"
_BATCH_SIZE = 500
INCREMENTAL_OVERLAP = timedelta(minutes=10)

DIFICULDADE_MIN_REPROVACOES = 2
DIFICULDADE_MIN_TAXA = 0.5
RECOMENDACAO_MIN_VIZINHOS = 2


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


@dataclass(frozen=True, slots=True)
class Step:
    """Um passo de carga: SQL de extração + cláusula Cypher de MERGE.

    ``incremental_sql`` recebe o mesmo número de ``%s`` que ``params_per_since``
    (todos preenchidos com o início da janela). ``None`` = sempre relido inteiro.
    """

    name: str
    full_sql: str
    merge: str
    incremental_sql: str | None = None
    params_per_since: int = 1
    transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None


# --------------------------------------------------------------------------- #
# SQL
# --------------------------------------------------------------------------- #

_POSTAGENS_WINDOW = "(p.data_postagem >= %s OR p.resolvido_em >= %s)"

_TENTATIVAS_AGG = """
    SELECT
        t.usuario_id,
        q.curso_id,
        COUNT(*) AS tentativas,
        SUM(CASE WHEN t.aprovado THEN 0 ELSE 1 END) AS reprovacoes,
        SUM(CASE WHEN t.aprovado THEN 1 ELSE 0 END) AS aprovacoes,
        AVG(t.nota) AS nota_media,
        MAX(t.nota) AS nota_maxima
    FROM tb_tentativas_quiz t
    JOIN tb_quizzes q ON q.id_quiz = t.quiz_id
    WHERE t.concluido_em IS NOT NULL
    {filtro}
    GROUP BY t.usuario_id, q.curso_id
"""
_TENTATIVAS_FILTRO_INCREMENTAL = """
    AND (t.usuario_id, q.curso_id) IN (
        SELECT t2.usuario_id, q2.curso_id
        FROM tb_tentativas_quiz t2
        JOIN tb_quizzes q2 ON q2.id_quiz = t2.quiz_id
        WHERE t2.concluido_em >= %s
    )
"""


def _desempenho_curso(row: dict[str, Any]) -> dict[str, Any]:
    tentativas = int(row["tentativas"])
    reprovacoes = int(row["reprovacoes"])
    row["tentativas"] = tentativas
    row["reprovacoes"] = reprovacoes
    row["aprovacoes"] = int(row["aprovacoes"])
    row["nota_media"] = None if row["nota_media"] is None else round(float(row["nota_media"]), 2)
    row["nota_maxima"] = _float(row["nota_maxima"])
    row["tem_dificuldade"] = (
        reprovacoes >= DIFICULDADE_MIN_REPROVACOES and tentativas > 0 and reprovacoes / tentativas >= DIFICULDADE_MIN_TAXA
    )
    row["aprovado"] = row["aprovacoes"] > 0
    return row


def _postagem(row: dict[str, Any]) -> dict[str, Any]:
    row["capturada_em"] = _iso(row.get("capturada_em"))
    return row


def _pertence(row: dict[str, Any]) -> dict[str, Any]:
    row["trust_score"] = _float(row.get("trust_score"))
    return row


NODE_STEPS: tuple[Step, ...] = (
    Step(
        "usuarios",
        """
        SELECT u.id_usuario AS id, u.nome_usuario AS nome, t.nome_tipo AS perfil
        FROM tb_usuarios u
        JOIN tb_lkp_tipos_usuarios t ON t.id_tipo_usuario = u.tipo_usuario_id
        """,
        "MERGE (n:Usuario {id: row.id}) SET n.nome = row.nome, n.perfil = row.perfil",
        incremental_sql="""
        SELECT u.id_usuario AS id, u.nome_usuario AS nome, t.nome_tipo AS perfil
        FROM tb_usuarios u
        JOIN tb_lkp_tipos_usuarios t ON t.id_tipo_usuario = u.tipo_usuario_id
        WHERE u.registro_em >= %s
        """,
    ),
    Step(
        "condominios",
        "SELECT c.id_condominio AS id, c.nome_condominio AS nome FROM tb_condominios c",
        "MERGE (n:Condominio {id: row.id}) SET n.nome = row.nome",
    ),
    Step(
        "torres",
        "SELECT t.id_torre AS id, t.nome_torre AS nome FROM tb_torres t",
        "MERGE (n:Torre {id: row.id}) SET n.nome = row.nome",
    ),
    Step(
        "cooperativas",
        "SELECT c.id_cooperativa AS id, c.nome_cooperativa AS nome FROM tb_cooperativas c",
        "MERGE (n:Cooperativa {id: row.id}) SET n.nome = row.nome",
        incremental_sql="""
        SELECT c.id_cooperativa AS id, c.nome_cooperativa AS nome
        FROM tb_cooperativas c WHERE c.data_cadastro >= %s
        """,
    ),
    Step(
        "categorias_residuo",
        "SELECT c.id_categoria AS id, c.nome_categoria AS nome FROM tb_lkp_categorias_residuos c",
        "MERGE (n:CategoriaResiduo {id: row.id}) SET n.nome = row.nome",
    ),
    Step(
        "cursos",
        "SELECT c.id_curso AS id, c.titulo_curso AS titulo, c.esta_ativo AS ativo FROM tb_cursos c",
        "MERGE (n:Curso {id: row.id}) SET n.titulo = row.titulo, n.ativo = row.ativo",
    ),
    Step(
        "postagens",
        """
        SELECT p.id_postagem AS id, p.capturada_em AS capturada_em, s.nome_status AS status_validacao
        FROM tb_postagens p
        JOIN tb_lkp_status_validacoes_postagens s ON s.id_status_validacao = p.status_validacao_id
        """,
        "MERGE (n:Postagem {id: row.id}) SET n.capturada_em = row.capturada_em, n.status_validacao = row.status_validacao",
        incremental_sql=f"""
        SELECT p.id_postagem AS id, p.capturada_em AS capturada_em, s.nome_status AS status_validacao
        FROM tb_postagens p
        JOIN tb_lkp_status_validacoes_postagens s ON s.id_status_validacao = p.status_validacao_id
        WHERE {_POSTAGENS_WINDOW}
        """,
        params_per_since=2,
        transform=_postagem,
    ),
)

EDGE_STEPS: tuple[Step, ...] = (
    Step(
        "mora_em",
        "SELECT m.usuario_id, m.condominio_id FROM tb_moradores m",
        "MATCH (u:Usuario {id: row.usuario_id}), (c:Condominio {id: row.condominio_id}) MERGE (u)-[:MORA_EM]->(c)",
    ),
    Step(
        "pertence_a_usuario_condominio",
        """
        SELECT r.usuario_id, r.condominio_id, r.trust_score,
               r.postagens_validadas_sem_contestacao, r.denuncias_realizadas, r.denuncias_procedentes
        FROM tb_rel_usuarios_condominios r
        """,
        "MATCH (u:Usuario {id: row.usuario_id}), (c:Condominio {id: row.condominio_id}) "
        "MERGE (u)-[r:PERTENCE_A]->(c) "
        "SET r.trust_score = row.trust_score, "
        "    r.postagens_validadas_sem_contestacao = row.postagens_validadas_sem_contestacao, "
        "    r.denuncias_realizadas = row.denuncias_realizadas, "
        "    r.denuncias_procedentes = row.denuncias_procedentes",
        transform=_pertence,
    ),
    Step(
        "torre_pertence_a_condominio",
        "SELECT t.id_torre AS torre_id, t.condominio_id FROM tb_torres t",
        "MATCH (t:Torre {id: row.torre_id}), (c:Condominio {id: row.condominio_id}) MERGE (t)-[:PERTENCE_A]->(c)",
    ),
    Step(
        "cooperativa_representada_por",
        "SELECT c.id_cooperativa AS cooperativa_id, c.usuario_id FROM tb_cooperativas c WHERE c.usuario_id IS NOT NULL",
        "MATCH (coop:Cooperativa {id: row.cooperativa_id}), (u:Usuario {id: row.usuario_id}) MERGE (coop)-[:REPRESENTADA_POR]->(u)",
        incremental_sql="""
        SELECT c.id_cooperativa AS cooperativa_id, c.usuario_id
        FROM tb_cooperativas c WHERE c.usuario_id IS NOT NULL AND c.data_cadastro >= %s
        """,
    ),
    Step(
        "postagem_relacoes",
        "SELECT p.id_postagem AS postagem_id, p.usuario_id, p.condominio_id, p.torre_id, p.categoria_id FROM tb_postagens p",
        # Uma leitura da tabela alimenta as quatro relações da postagem.
        "MATCH (p:Postagem {id: row.postagem_id}) "
        "OPTIONAL MATCH (u:Usuario {id: row.usuario_id}) "
        "OPTIONAL MATCH (c:Condominio {id: row.condominio_id}) "
        "OPTIONAL MATCH (cat:CategoriaResiduo {id: row.categoria_id}) "
        "OPTIONAL MATCH (t:Torre {id: row.torre_id}) "
        "FOREACH (_ IN CASE WHEN u IS NULL THEN [] ELSE [1] END | MERGE (u)-[:CRIOU]->(p)) "
        "FOREACH (_ IN CASE WHEN c IS NULL THEN [] ELSE [1] END | MERGE (p)-[:NO_CONDOMINIO]->(c)) "
        "FOREACH (_ IN CASE WHEN cat IS NULL THEN [] ELSE [1] END | MERGE (p)-[:DA_CATEGORIA]->(cat)) "
        "FOREACH (_ IN CASE WHEN t IS NULL THEN [] ELSE [1] END | MERGE (p)-[:NA_TORRE]->(t))",
        incremental_sql=f"""
        SELECT p.id_postagem AS postagem_id, p.usuario_id, p.condominio_id, p.torre_id, p.categoria_id
        FROM tb_postagens p WHERE {_POSTAGENS_WINDOW}
        """,
        params_per_since=2,
    ),
    Step(
        "validou",
        """
        SELECT v.usuario_id, v.postagem_id, v.peso_aplicado AS peso, t.nome_tipo AS tipo
        FROM tb_rel_votos_postagens v
        JOIN tb_lkp_tipos_votos_postagens t ON t.id_tipo_voto = v.tipo_voto_id
        WHERE v.motivo_denuncia_id IS NULL
        """,
        "MATCH (u:Usuario {id: row.usuario_id}), (p:Postagem {id: row.postagem_id}) "
        "MERGE (u)-[r:VALIDOU]->(p) SET r.peso = row.peso, r.tipo = row.tipo",
        incremental_sql="""
        SELECT v.usuario_id, v.postagem_id, v.peso_aplicado AS peso, t.nome_tipo AS tipo
        FROM tb_rel_votos_postagens v
        JOIN tb_lkp_tipos_votos_postagens t ON t.id_tipo_voto = v.tipo_voto_id
        WHERE v.motivo_denuncia_id IS NULL AND v.votado_em >= %s
        """,
    ),
    Step(
        "denunciou",
        """
        SELECT v.usuario_id, v.postagem_id, v.peso_aplicado AS peso, m.descricao AS motivo
        FROM tb_rel_votos_postagens v
        JOIN tb_lkp_motivos_denuncia m ON m.id_motivo_denuncia = v.motivo_denuncia_id
        """,
        "MATCH (u:Usuario {id: row.usuario_id}), (p:Postagem {id: row.postagem_id}) "
        "MERGE (u)-[r:DENUNCIOU]->(p) SET r.peso = row.peso, r.motivo = row.motivo",
        incremental_sql="""
        SELECT v.usuario_id, v.postagem_id, v.peso_aplicado AS peso, m.descricao AS motivo
        FROM tb_rel_votos_postagens v
        JOIN tb_lkp_motivos_denuncia m ON m.id_motivo_denuncia = v.motivo_denuncia_id
        WHERE v.votado_em >= %s
        """,
    ),
    Step(
        "desempenho_cursos",
        _TENTATIVAS_AGG.format(filtro=""),
        # Cria/atualiza ou REMOVE as arestas conforme a regra atual; assim quem
        # melhora deixa de aparecer com dificuldade.
        "MATCH (u:Usuario {id: row.usuario_id}), (c:Curso {id: row.curso_id}) "
        "OPTIONAL MATCH (u)-[old_d:TEM_DIFICULDADE_EM]->(c) "
        "OPTIONAL MATCH (u)-[old_a:APROVADO_EM]->(c) "
        "FOREACH (_ IN CASE WHEN row.tem_dificuldade THEN [1] ELSE [] END | "
        "  MERGE (u)-[d:TEM_DIFICULDADE_EM]->(c) "
        "  SET d.tentativas = row.tentativas, d.reprovacoes = row.reprovacoes, d.nota_media = row.nota_media) "
        "FOREACH (_ IN CASE WHEN NOT row.tem_dificuldade AND old_d IS NOT NULL THEN [1] ELSE [] END | DELETE old_d) "
        "FOREACH (_ IN CASE WHEN row.aprovado THEN [1] ELSE [] END | "
        "  MERGE (u)-[a:APROVADO_EM]->(c) SET a.nota_maxima = row.nota_maxima) "
        "FOREACH (_ IN CASE WHEN NOT row.aprovado AND old_a IS NOT NULL THEN [1] ELSE [] END | DELETE old_a)",
        incremental_sql=_TENTATIVAS_AGG.format(filtro=_TENTATIVAS_FILTRO_INCREMENTAL),
        transform=_desempenho_curso,
    ),
)


# Recomendações: calculadas sobre o grafo já atualizado. `run_id` marca o que
# esta execução confirmou; o que sobrar de execuções antigas é removido no fim.
RECOMENDACAO_VIZINHOS = """
MATCH (u:Usuario)-[:MORA_EM|PERTENCE_A]->(cond:Condominio)<-[:MORA_EM|PERTENCE_A]-(peer:Usuario)-[:APROVADO_EM]->(c:Curso)
WHERE peer <> u AND coalesce(c.ativo, true) AND NOT (u)-[:APROVADO_EM]->(c)
WITH u, c, count(DISTINCT peer) AS vizinhos
WHERE vizinhos >= $min_vizinhos
MERGE (c)-[r:RECOMENDADO_PARA]->(u)
SET r.motivo = 'vizinhos_aprovados', r.score = toFloat(vizinhos), r.run_id = $run_id, r.atualizado_em = $agora
RETURN count(r) AS total
"""
RECOMENDACAO_REFORCO = """
MATCH (u:Usuario)-[d:TEM_DIFICULDADE_EM]->(c:Curso)
WHERE coalesce(c.ativo, true)
MERGE (c)-[r:RECOMENDADO_PARA]->(u)
SET r.motivo = 'reforco', r.score = toFloat(d.reprovacoes), r.run_id = $run_id, r.atualizado_em = $agora
RETURN count(r) AS total
"""
RECOMENDACAO_LIMPEZA = """
MATCH ()-[r:RECOMENDADO_PARA]->()
WHERE r.run_id IS NULL OR r.run_id <> $run_id
DELETE r
RETURN count(*) AS total
"""
PRUNE_POSTAGENS = """
MATCH (p:Postagem) WHERE NOT p.id IN $ids
DETACH DELETE p
RETURN count(*) AS total
"""


class GraphPopulator:
    """Extrai do PostgreSQL e faz MERGE idempotente no Neo4j em lotes (`UNWIND $rows`)."""

    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self.postgres = postgres_db
        self.neo4j = neo4j_db
        self._now = now or (lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------------ #
    # Execução
    # ------------------------------------------------------------------ #

    async def run(self, mode: str = "full", *, prune: bool = False) -> dict[str, Any]:
        if mode not in {"full", "incremental"}:
            raise ValueError("mode deve ser 'full' ou 'incremental'")
        started_at = self._now()
        run_id = uuid.uuid4().hex
        await self._ensure_constraints()

        since: datetime | None = None
        if mode == "incremental":
            last = await self._read_checkpoint()
            if last is None:
                logger.info("etl_grafo_sem_checkpoint_rodando_full")
                mode = "full"
            else:
                since = last - INCREMENTAL_OVERLAP

        stats: dict[str, Any] = {"modo": mode, "desde": _iso(since)}
        for step in (*NODE_STEPS, *EDGE_STEPS):
            stats[step.name] = await self._run_step(step, since)

        if prune and mode == "full":
            stats["postagens_removidas"] = await self._prune_postagens()

        stats.update(await self._recommendations(run_id, started_at))
        await self._write_checkpoint(started_at, mode, stats)
        logger.info("etl_grafo_concluido", extra={"stats": stats})
        return stats

    async def _run_step(self, step: Step, since: datetime | None) -> int:
        if since is not None and step.incremental_sql is not None:
            rows = self.postgres.fetch_all(step.incremental_sql, (since,) * step.params_per_since)
        else:
            rows = self.postgres.fetch_all(step.full_sql)
        if step.transform is not None:
            rows = [step.transform(dict(row)) for row in rows]
        return await self._run_batched(step.merge, rows, log_label=step.name)

    # ------------------------------------------------------------------ #
    # Constraints e checkpoint
    # ------------------------------------------------------------------ #

    async def _ensure_constraints(self) -> None:
        """Uma constraint de unicidade por label (a chave de todo `MERGE`).
        Sem ela, cada `MERGE {id: ...}` varre o label inteiro."""
        for label in NODE_LABELS:
            await self.neo4j.execute(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE")
        await self.neo4j.execute(
            "CREATE CONSTRAINT IF NOT EXISTS FOR (n:EtlCheckpoint) REQUIRE n.pipeline IS UNIQUE"
        )

    async def _read_checkpoint(self) -> datetime | None:
        rows = await self.neo4j.execute(
            "MATCH (c:EtlCheckpoint {pipeline: $pipeline}) RETURN c.last_success_at AS last",
            {"pipeline": PIPELINE},
        )
        if not rows or not rows[0].get("last"):
            return None
        return datetime.fromisoformat(str(rows[0]["last"]))

    async def _write_checkpoint(self, started_at: datetime, mode: str, stats: dict[str, Any]) -> None:
        # Grava o INÍCIO da execução: o que entrou no Postgres durante a carga
        # cai na próxima janela (mais a sobreposição).
        await self.neo4j.execute(
            "MERGE (c:EtlCheckpoint {pipeline: $pipeline}) "
            "SET c.last_success_at = $at, c.last_mode = $mode, c.finished_at = $finished, c.rows = $rows",
            {
                "pipeline": PIPELINE,
                "at": started_at.isoformat(),
                "mode": mode,
                "finished": self._now().isoformat(),
                "rows": sum(v for v in stats.values() if isinstance(v, int)),
            },
        )

    # ------------------------------------------------------------------ #
    # Derivados
    # ------------------------------------------------------------------ #

    async def _recommendations(self, run_id: str, now: datetime) -> dict[str, int]:
        params = {"run_id": run_id, "agora": now.isoformat(), "min_vizinhos": RECOMENDACAO_MIN_VIZINHOS}
        vizinhos = await self.neo4j.execute(RECOMENDACAO_VIZINHOS, params)
        # Reforço roda depois: para quem tem dificuldade, o motivo mais específico prevalece.
        reforco = await self.neo4j.execute(RECOMENDACAO_REFORCO, params)
        removidas = await self.neo4j.execute(RECOMENDACAO_LIMPEZA, {"run_id": run_id})
        return {
            "recomendacoes_vizinhos": _count(vizinhos),
            "recomendacoes_reforco": _count(reforco),
            "recomendacoes_removidas": _count(removidas),
        }

    async def _prune_postagens(self) -> int:
        ids = [row["id"] for row in self.postgres.fetch_all("SELECT p.id_postagem AS id FROM tb_postagens p")]
        return _count(await self.neo4j.execute(PRUNE_POSTAGENS, {"ids": ids}))

    # ------------------------------------------------------------------ #
    # Escrita em lote
    # ------------------------------------------------------------------ #

    async def _run_batched(self, merge_clause: str, rows: list[dict[str, Any]], *, log_label: str) -> int:
        total = 0
        query = f"UNWIND $rows AS row {merge_clause}"
        for start in range(0, len(rows), _BATCH_SIZE):
            batch = rows[start : start + _BATCH_SIZE]
            await self.neo4j.execute(query, {"rows": batch})
            total += len(batch)
        logger.info("etl_grafo_lote", extra={"passo": log_label, "total": total})
        return total


def _count(rows: list[dict[str, Any]]) -> int:
    return int(rows[0].get("total", 0)) if rows else 0


# --------------------------------------------------------------------------- #
# CLI / agendamento
# --------------------------------------------------------------------------- #


async def run_once(mode: str, *, prune: bool = False) -> dict[str, Any]:
    postgres_db.start(settings)
    await neo4j_db.start(settings)
    try:
        return await GraphPopulator().run(mode, prune=prune)
    finally:
        await neo4j_db.close()
        postgres_db.close()


async def run_forever(
    interval_seconds: int,
    full_every_hours: float,
    *,
    runner: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    iterations: int | None = None,
) -> None:
    """Incremental a cada `interval_seconds`; full a cada `full_every_hours`.
    Falha de uma execução é logada e a próxima tenta de novo (o checkpoint só
    avança em sucesso, então nada se perde)."""
    runner = runner or (lambda mode: run_once(mode))
    last_full = float("-inf")
    done = 0
    while iterations is None or done < iterations:
        mode = "full" if time.monotonic() - last_full >= full_every_hours * 3600 else "incremental"
        try:
            stats = await runner(mode)
            if stats.get("modo") == "full":
                last_full = time.monotonic()
            logger.info("etl_grafo_ciclo_ok", extra={"modo": stats.get("modo")})
        except Exception:
            logger.exception("etl_grafo_ciclo_falhou")
        done += 1
        if iterations is None or done < iterations:
            await sleep(interval_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Povoa o grafo Neo4j a partir do PostgreSQL.")
    parser.add_argument("--mode", choices=("full", "incremental"), default="incremental")
    parser.add_argument("--prune", action="store_true", help="(full) remove postagens que não existem mais no Postgres")
    parser.add_argument("--loop-seconds", type=int, default=0, help="roda para sempre com este intervalo (0 = uma vez)")
    parser.add_argument("--full-every-hours", type=float, default=24.0, help="no modo loop, frequência do full")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    if args.loop_seconds > 0:
        asyncio.run(run_forever(args.loop_seconds, args.full_every_hours))
        return 0
    stats = asyncio.run(run_once(args.mode, prune=args.prune))
    for chave, valor in stats.items():
        print(f"{chave}: {valor}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
