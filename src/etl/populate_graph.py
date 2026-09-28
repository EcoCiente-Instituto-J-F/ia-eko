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
(Usuario)-[:MORA_EM]->(Condominio)            tb_moradores                     usuário novo OU vínculo auditado
(Usuario)-[:PERTENCE_A {trust_score…}]->(C)   tb_rel_usuarios_condominios      auditoria do vínculo (todo UPDATE)
                                              (só aprovado e sem data_saida)
(Torre)-[:PERTENCE_A]->(Condominio)           tb_torres.condominio_id          — (dimensão pequena)
(Cooperativa)-[:REPRESENTADA_POR]->(Usuario)  tb_cooperativas.usuario_id       data_cadastro
(Usuario)-[:CRIOU]->(Postagem)                tb_postagens                     data_postagem OU resolvido_em
(Postagem)-[:NO_CONDOMINIO|NA_TORRE|DA_CATEGORIA]  tb_postagens                data_postagem OU resolvido_em
(Usuario)-[:VALIDOU {peso, tipo}]->(Postagem)  tb_rel_votos_postagens (sem motivo)  votado_em
(Usuario)-[:DENUNCIOU {peso, motivo}]->(P)     tb_rel_votos_postagens (com motivo)  votado_em
(Usuario)-[:APROVADO_EM]->(Curso)             tb_tentativas_quiz + tb_quizzes  pares (usuário, curso) com tentativa nova
(Usuario)-[:TEM_DIFICULDADE_EM]->(Curso)      tb_tentativas_quiz + tb_quizzes  pares (usuário, curso) com tentativa nova
(Curso)-[:RECOMENDADO_PARA]->(Usuario)        calculada no próprio grafo       só no full (derivada; consulta pesada)
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

Exclusões
---------
- Saída do condomínio (data_saida) ou vínculo recusado: o passo de PERTENCE_A
  remove a aresta; mudança de condomínio troca o MORA_EM na mesma linha.
- No schema atual postagens e vínculos NÃO podem ser apagados: a linha de
  auditoria do INSERT referencia a linha original por FK sem ON DELETE, então
  todo DELETE falha (testado em Postgres 16). Por isso não há propagação de
  DELETE no incremental. Se o schema mudar, o ``full`` com ``--prune``
  reconcilia postagens, PERTENCE_A e MORA_EM (diferença calculada em Python, O(n)).

Leitura em lotes com cursor do servidor (``iter_batches``): memória constante.
Medido em Postgres 16 real com o schema do projeto: 1,8 mi de votos, 600 mil
postagens e 200 mil usuários → full lê tudo em ~38 s e o processo não passa
do tamanho de um lote; antes, ``fetch_all`` chegava a 1,4 GB.

Limitações conhecidas
---------------------
- Duas execuções simultâneas não são bloqueadas aqui: no Kubernetes, os
  CronJobs usam ``concurrencyPolicy: Forbid``; no modo loop há um único processo.

Uso
---
    python -m src.etl.populate_graph --mode full
    python -m src.etl.populate_graph --mode incremental
    python -m src.etl.populate_graph --loop-seconds 900 --full-every-hours 24
    python -m src.etl.populate_graph --validar --relatorio etl_validacao.json
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Iterator

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

# Postagem nova, resolvida, ou alterada de qualquer outro jeito (status,
# categoria corrigida na moderação, torre...): todo UPDATE passa pelo trigger
# fn_trg_auditoria_postagens, que carimba a data.
_POSTAGENS_WINDOW = """(
    p.data_postagem >= %s OR p.resolvido_em >= %s
    OR p.id_postagem IN (
        SELECT l.postagem_id FROM tb_log_auditoria_postagens l
        JOIN tb_log_auditoria a ON a.id_auditoria = l.auditoria_id
        WHERE a.executado_em >= %s
    )
)"""

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


# Vínculos usuário×condomínio tocados na janela, pela auditoria do schema
# (trigger fn_trg_auditoria_usuarios_condominios: INSERT/UPDATE/DELETE).
_VINCULOS_ALTERADOS = """
    SELECT l.usuario_condominio_id
    FROM tb_log_auditoria_usuarios_condominios l
    JOIN tb_log_auditoria a ON a.id_auditoria = l.auditoria_id
    WHERE a.executado_em >= %s
"""
_VINCULO_COLUNAS = (
    "r.id_usuario_condominio AS vinculo_id, r.usuario_id, r.condominio_id, r.aprovado, "
    "r.data_saida, r.trust_score, r.postagens_validadas_sem_contestacao, "
    "r.denuncias_realizadas, r.denuncias_procedentes"
)

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
    aprovado = bool(row.pop("aprovado", True))
    saiu = row.pop("data_saida", None) is not None
    row["ativo"] = aprovado and not saiu
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
        params_per_since=3,
        transform=_postagem,
    ),
)

EDGE_STEPS: tuple[Step, ...] = (
    Step(
        "mora_em",
        "SELECT m.usuario_id, m.condominio_id FROM tb_moradores m",
        # tb_moradores tem UNIQUE(usuario_id): um MORA_EM por usuário. Se ele
        # mudou de condomínio, a aresta antiga sai na mesma linha.
        "MATCH (u:Usuario {id: row.usuario_id}), (c:Condominio {id: row.condominio_id}) "
        "OPTIONAL MATCH (u)-[old:MORA_EM]->(antigo:Condominio) WHERE antigo <> c "
        "WITH u, c, collect(old) AS antigas "
        "FOREACH (x IN antigas | DELETE x) "
        "MERGE (u)-[:MORA_EM]->(c)",
        # tb_moradores não tem data nem auditoria. No incremental vão os
        # moradores novos (registro_em) e os que tiveram o vínculo com o
        # condomínio alterado (auditoria); o resto é reconciliado no full diário.
        incremental_sql=f"""
        SELECT m.usuario_id, m.condominio_id
        FROM tb_moradores m
        JOIN tb_usuarios u ON u.id_usuario = m.usuario_id
        WHERE u.registro_em >= %s
           OR m.usuario_id IN (
                SELECT r.usuario_id FROM tb_rel_usuarios_condominios r
                WHERE r.id_usuario_condominio IN ({_VINCULOS_ALTERADOS})
           )
        """,
        params_per_since=2,
    ),
    Step(
        "pertence_a_usuario_condominio",
        f"SELECT {_VINCULO_COLUNAS} FROM tb_rel_usuarios_condominios r",
        # Só vínculo aprovado e sem data_saida vira PERTENCE_A; quem saiu ou foi
        # recusado perde a aresta (antes ficava para sempre e contava como
        # "vizinho" nas recomendações).
        "MATCH (u:Usuario {id: row.usuario_id}), (c:Condominio {id: row.condominio_id}) "
        "OPTIONAL MATCH (u)-[old:PERTENCE_A]->(c) "
        "FOREACH (_ IN CASE WHEN row.ativo THEN [1] ELSE [] END | "
        "  MERGE (u)-[r:PERTENCE_A]->(c) "
        "  SET r.vinculo_id = row.vinculo_id, r.trust_score = row.trust_score, "
        "      r.postagens_validadas_sem_contestacao = row.postagens_validadas_sem_contestacao, "
        "      r.denuncias_realizadas = row.denuncias_realizadas, "
        "      r.denuncias_procedentes = row.denuncias_procedentes) "
        "FOREACH (_ IN CASE WHEN NOT row.ativo AND old IS NOT NULL THEN [1] ELSE [] END | DELETE old)",
        # Todo UPDATE em tb_rel_usuarios_condominios (inclusive do trust_score)
        # passa pelo trigger de auditoria, que carimba a data.
        incremental_sql=f"""
        SELECT {_VINCULO_COLUNAS} FROM tb_rel_usuarios_condominios r
        WHERE r.id_usuario_condominio IN ({_VINCULOS_ALTERADOS})
        """,
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
        # Categoria/torre/condomínio corrigidos: a aresta antiga sai.
        "OPTIONAL MATCH (p)-[old:NO_CONDOMINIO|NA_TORRE|DA_CATEGORIA]->(alvo) "
        "WITH row, p, [r IN collect(old) WHERE NOT ("
        "  (type(r) = 'NO_CONDOMINIO' AND coalesce(endNode(r).id = row.condominio_id, false)) OR "
        "  (type(r) = 'NA_TORRE' AND coalesce(endNode(r).id = row.torre_id, false)) OR "
        "  (type(r) = 'DA_CATEGORIA' AND coalesce(endNode(r).id = row.categoria_id, false))"
        ")] AS obsoletas "
        "FOREACH (r IN obsoletas | DELETE r) "
        "WITH row, p "
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
        params_per_since=3,
    ),
    Step(
        "validou",
        """
        SELECT v.usuario_id, v.postagem_id, v.peso_aplicado AS peso, t.nome_tipo AS tipo
        FROM tb_rel_votos_postagens v
        JOIN tb_lkp_tipos_votos_postagens t ON t.id_tipo_voto = v.tipo_voto_id
        WHERE v.motivo_denuncia_id IS NULL
        """,
        # Um voto por (postagem, usuário): se virou validação, a denúncia antiga sai.
        "MATCH (u:Usuario {id: row.usuario_id}), (p:Postagem {id: row.postagem_id}) "
        "OPTIONAL MATCH (u)-[old:DENUNCIOU]->(p) "
        "WITH row, u, p, collect(old) AS antigas FOREACH (x IN antigas | DELETE x) "
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
        "OPTIONAL MATCH (u)-[old:VALIDOU]->(p) "
        "WITH row, u, p, collect(old) AS antigas FOREACH (x IN antigas | DELETE x) "
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


# Recomendações: calculadas sobre o grafo já atualizado. "Vizinho" = vínculo
# ativo (PERTENCE_A: aprovado e sem data_saida). MORA_EM não entra: vem de
# tb_moradores, que não sabe se a pessoa saiu ou foi recusada. `run_id` marca o que
# esta execução confirmou; o que sobrar de execuções antigas é removido no fim.
RECOMENDACAO_VIZINHOS = """
MATCH (u:Usuario)-[:PERTENCE_A]->(cond:Condominio)<-[:PERTENCE_A]-(peer:Usuario)-[:APROVADO_EM]->(c:Curso)
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
        duracoes: dict[str, float] = {}
        for step in (*NODE_STEPS, *EDGE_STEPS):
            t0 = time.perf_counter()
            stats[step.name] = await self._run_step(step, since)
            duracoes[step.name] = round(time.perf_counter() - t0, 3)

        if prune and mode == "full":
            t0 = time.perf_counter()
            stats.update(await self._prune())
            duracoes["prune"] = round(time.perf_counter() - t0, 3)

        # A consulta de vizinhos percorre o grafo inteiro (moradores² por
        # condomínio × cursos). Roda só no full diário; no incremental de 15 em
        # 15 min ela dominaria o custo no Aura. TEM_DIFICULDADE_EM/APROVADO_EM
        # continuam atualizadas a cada incremental.
        if mode == "full":
            t0 = time.perf_counter()
            stats.update(await self._recommendations(run_id, started_at))
            duracoes["recomendacoes"] = round(time.perf_counter() - t0, 3)
        duracoes["total"] = round(sum(duracoes.values()), 3)
        stats["duracao_s"] = duracoes
        await self._write_checkpoint(started_at, mode, stats)
        logger.info("etl_grafo_concluido", extra={"stats": stats})
        return stats

    async def _run_step(self, step: Step, since: datetime | None) -> int:
        if since is not None and step.incremental_sql is not None:
            sql, params = step.incremental_sql, (since,) * step.params_per_since
        else:
            sql, params = step.full_sql, ()
        query = f"UNWIND $rows AS row {step.merge}"
        total = 0
        # Lê e grava lote a lote: memória constante, qualquer volume.
        for batch in self._batches(sql, params):
            if step.transform is not None:
                batch = [step.transform(dict(row)) for row in batch]
            await self.neo4j.execute(query, {"rows": batch})
            total += len(batch)
        logger.info("etl_grafo_lote", extra={"passo": step.name, "total": total})
        return total

    def _batches(self, sql: str, params: tuple = ()) -> Iterator[list[dict[str, Any]]]:
        iter_batches = getattr(self.postgres, "iter_batches", None)
        if iter_batches is not None:
            yield from iter_batches(sql, params, _BATCH_SIZE)
            return
        rows = self.postgres.fetch_all(sql, params)
        for start in range(0, len(rows), _BATCH_SIZE):
            yield rows[start : start + _BATCH_SIZE]

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

    async def _prune(self) -> dict[str, int]:
        """Remove do grafo o que não existe mais no Postgres.

        A versão anterior mandava TODOS os ids num `NOT p.id IN $ids`, que o
        Neo4j avalia varrendo a lista para cada nó (O(n²): com 600 mil
        postagens, bilhões de comparações). Aqui a diferença é calculada em
        Python com conjuntos, e o grafo só recebe os ids a apagar, que ele
        encontra pelo índice da constraint."""
        existentes = {row["id"] for batch in self._batches("SELECT p.id_postagem AS id FROM tb_postagens p") for row in batch}
        orfas = [pid for pid in await self._graph_ids("Postagem") if pid not in existentes]
        await self._run_batched("MATCH (p:Postagem {id: row}) DETACH DELETE p", orfas, log_label="prune_postagens")

        pertence = {
            (row["usuario_id"], row["condominio_id"])
            for batch in self._batches(
                "SELECT r.usuario_id, r.condominio_id FROM tb_rel_usuarios_condominios r "
                "WHERE r.aprovado AND r.data_saida IS NULL"
            )
            for row in batch
        }
        mora = {
            (row["usuario_id"], row["condominio_id"])
            for batch in self._batches("SELECT m.usuario_id, m.condominio_id FROM tb_moradores m")
            for row in batch
        }
        sobra_pertence: list[dict[str, int]] = []
        sobra_mora: list[dict[str, int]] = []
        async for usuario_id, pertence_grafo, mora_grafo in self._graph_memberships():
            sobra_pertence += [{"u": usuario_id, "c": c} for c in pertence_grafo if (usuario_id, c) not in pertence]
            sobra_mora += [{"u": usuario_id, "c": c} for c in mora_grafo if (usuario_id, c) not in mora]
        await self._run_batched(
            "MATCH (:Usuario {id: row.u})-[r:PERTENCE_A]->(:Condominio {id: row.c}) DELETE r",
            sobra_pertence,
            log_label="prune_pertence_a",
        )
        await self._run_batched(
            "MATCH (:Usuario {id: row.u})-[r:MORA_EM]->(:Condominio {id: row.c}) DELETE r",
            sobra_mora,
            log_label="prune_mora_em",
        )
        return {
            "postagens_removidas": len(orfas),
            "pertence_a_removidas": len(sobra_pertence),
            "mora_em_removidas": len(sobra_mora),
        }

    async def _graph_ids(self, label: str, page: int = 10_000) -> list[Any]:
        """Ids de um label, paginados pela ordem do índice da constraint."""
        ids: list[Any] = []
        after: Any = None
        while True:
            # Sem "OR $after IS NULL": com o predicado simples o planner usa o
            # índice da constraint para o ORDER BY/LIMIT em vez de ordenar tudo.
            filtro = "" if after is None else "WHERE n.id > $after "
            rows = await self.neo4j.execute(
                f"MATCH (n:{label}) {filtro}RETURN n.id AS id ORDER BY n.id LIMIT $page",
                {"after": after, "page": page},
            )
            if not rows:
                return ids
            ids.extend(row["id"] for row in rows)
            after = rows[-1]["id"]
            if len(rows) < page:
                return ids

    async def _graph_memberships(self, page: int = 5_000):
        after: Any = None
        while True:
            filtro = "" if after is None else "WHERE u.id > $after "
            rows = await self.neo4j.execute(
                f"MATCH (u:Usuario) {filtro}"
                "WITH u ORDER BY u.id LIMIT $page "
                "OPTIONAL MATCH (u)-[:PERTENCE_A]->(pc:Condominio) "
                "OPTIONAL MATCH (u)-[:MORA_EM]->(mc:Condominio) "
                "RETURN u.id AS id, collect(DISTINCT pc.id) AS pertence, collect(DISTINCT mc.id) AS mora "
                "ORDER BY id",
                {"after": after, "page": page},
            )
            if not rows:
                return
            for row in rows:
                yield row["id"], row.get("pertence") or [], row.get("mora") or []
            after = rows[-1]["id"]
            if len(rows) < page:
                return

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


async def validate_once() -> dict[str, Any]:
    from src.etl.validate_graph import validar

    postgres_db.start(settings)
    await neo4j_db.start(settings)
    try:
        return await validar(GraphPopulator())
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
    parser.add_argument(
        "--validar",
        action="store_true",
        help="full + conferência de contagens Postgres×grafo + full de novo (idempotência) + incremental, com tempos",
    )
    parser.add_argument("--relatorio", default=None, help="com --validar, grava o relatório JSON neste arquivo")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    if args.validar:
        from src.etl.validate_graph import imprimir

        relatorio = asyncio.run(validate_once())
        imprimir(relatorio, args.relatorio)
        return 0 if relatorio["ok"] else 1

    if args.loop_seconds > 0:
        asyncio.run(run_forever(args.loop_seconds, args.full_every_hours))
        return 0
    stats = asyncio.run(run_once(args.mode, prune=args.prune))
    for chave, valor in stats.items():
        print(f"{chave}: {valor}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
