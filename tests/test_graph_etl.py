from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import src.etl.populate_graph as etl
from src.etl.graph_model import NODE_LABELS, RELATIONSHIP_TYPES
from src.etl.populate_graph import (
    EDGE_STEPS,
    INCREMENTAL_OVERLAP,
    NODE_STEPS,
    GraphPopulator,
    Step,
    _desempenho_curso,
    run_forever,
)

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
SCHEMA = Path(__file__).resolve().parents[1] / "sql" / "ecociente_schema.sql"


class FakePostgres:
    """Responde por tabela e registra (sql, params) de cada consulta."""

    def __init__(self, table_rows: dict[str, list[dict]] | None = None):
        self.table_rows = table_rows or {}
        self.queries: list[tuple[str, tuple]] = []

    def fetch_all(self, query: str, params=()):
        self.queries.append((query, tuple(params)))
        for table, rows in self.table_rows.items():
            if re.search(rf"\bFROM\s+{table}\b", query):
                return [dict(r) for r in rows]
        return []


class FakeNeo4j:
    def __init__(self, checkpoint: datetime | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.checkpoint = checkpoint

    async def execute(self, query: str, parameters=None):
        self.calls.append((query, parameters or {}))
        if "MATCH (c:EtlCheckpoint" in query and "RETURN c.last_success_at" in query:
            return [{"last": self.checkpoint.isoformat()}] if self.checkpoint else []
        if "RETURN count" in query:
            return [{"total": 3}]
        return []

    def queries(self, contains: str) -> list[tuple[str, dict]]:
        return [call for call in self.calls if contains in call[0]]


def _populator(pg: FakePostgres, neo: FakeNeo4j) -> GraphPopulator:
    populator = GraphPopulator(now=lambda: NOW)
    populator.postgres = pg
    populator.neo4j = neo
    return populator


# ------------------------------------------------------------ constraints


@pytest.mark.asyncio
async def test_constraints_for_every_label_and_checkpoint():
    neo = FakeNeo4j()
    await _populator(FakePostgres(), neo)._ensure_constraints()
    for label in NODE_LABELS:
        assert neo.queries(f"FOR (n:{label}) REQUIRE n.id IS UNIQUE")
    assert neo.queries("FOR (n:EtlCheckpoint) REQUIRE n.pipeline IS UNIQUE")


def test_every_merged_label_has_a_constraint():
    merged = set()
    for step in (*NODE_STEPS, *EDGE_STEPS):
        merged.update(re.findall(r"MERGE \(n:(\w+) \{id:", step.merge))
    assert merged <= set(NODE_LABELS)


def test_graph_vocabulary_matches_agent_whitelist():
    from src.agents.graph_traversal import tools

    assert tools._LABELS_PERMITIDOS == set(NODE_LABELS)
    assert tools._RELACOES_PERMITIDAS == set(RELATIONSHIP_TYPES)
    written = set()
    for step in EDGE_STEPS:
        written.update(re.findall(r"\[(?:\w+)?:([A-Z_]+)\]", step.merge))
    written.add("RECOMENDADO_PARA")
    assert written == set(RELATIONSHIP_TYPES)


# ----------------------------------------------------------------- modos


@pytest.mark.asyncio
async def test_full_mode_reads_whole_tables_and_writes_checkpoint():
    pg = FakePostgres({"tb_moradores": [{"usuario_id": 1, "condominio_id": 10}]})
    neo = FakeNeo4j()
    stats = await _populator(pg, neo).run("full")

    assert stats["modo"] == "full"
    assert stats["mora_em"] == 1
    assert all(params == () for _, params in pg.queries), "full não usa janela de data"
    checkpoint = neo.queries("MERGE (c:EtlCheckpoint")
    assert checkpoint and checkpoint[-1][1]["at"] == NOW.isoformat()


@pytest.mark.asyncio
async def test_incremental_without_checkpoint_falls_back_to_full():
    stats = await _populator(FakePostgres(), FakeNeo4j(checkpoint=None)).run("incremental")
    assert stats["modo"] == "full"


@pytest.mark.asyncio
async def test_incremental_uses_window_with_overlap():
    last = NOW - timedelta(hours=1)
    pg = FakePostgres()
    stats = await _populator(pg, FakeNeo4j(checkpoint=last)).run("incremental")

    since = last - INCREMENTAL_OVERLAP
    assert stats["modo"] == "incremental"
    assert stats["desde"] == since.isoformat()
    windowed = [(sql, params) for sql, params in pg.queries if params]
    assert windowed, "passos incrementais devem receber a janela"
    for sql, params in windowed:
        assert sql.count("%s") == len(params)
        assert all(value == since for value in params)
    # Tabelas sem coluna de data continuam sendo relidas inteiras.
    assert any("FROM tb_moradores" in sql and not params for sql, params in pg.queries)


@pytest.mark.asyncio
async def test_failure_does_not_advance_checkpoint():
    class Boom(FakePostgres):
        def fetch_all(self, query, params=()):
            if "tb_rel_votos_postagens" in query:
                raise RuntimeError("postgres caiu")
            return super().fetch_all(query, params)

    neo = FakeNeo4j()
    with pytest.raises(RuntimeError):
        await _populator(Boom(), neo).run("full")
    assert not neo.queries("MERGE (c:EtlCheckpoint")


@pytest.mark.asyncio
async def test_batches_respect_batch_size(monkeypatch):
    rows = [{"usuario_id": i, "condominio_id": 10} for i in range(1200)]
    neo = FakeNeo4j()
    monkeypatch.setattr(etl, "_BATCH_SIZE", 500)
    step = next(s for s in EDGE_STEPS if s.name == "mora_em")
    total = await _populator(FakePostgres({"tb_moradores": rows}), neo)._run_step(step, None)
    assert total == 1200
    assert len(neo.queries("MERGE (u)-[:MORA_EM]->(c)")) == 3


@pytest.mark.asyncio
async def test_trust_score_decimal_becomes_float():
    step = next(s for s in EDGE_STEPS if s.name == "pertence_a_usuario_condominio")
    neo = FakeNeo4j()
    pg = FakePostgres(
        {
            "tb_rel_usuarios_condominios": [
                {
                    "usuario_id": 1,
                    "condominio_id": 10,
                    "trust_score": "87.50",
                    "postagens_validadas_sem_contestacao": 3,
                    "denuncias_realizadas": 1,
                    "denuncias_procedentes": 1,
                }
            ]
        }
    )
    await _populator(pg, neo)._run_step(step, None)
    assert neo.calls[0][1]["rows"][0]["trust_score"] == 87.5


# ------------------------------------------------------ regras derivadas


@pytest.mark.parametrize(
    ("tentativas", "reprovacoes", "aprovacoes", "dificuldade", "aprovado"),
    [
        (2, 2, 0, True, False),   # 2 reprovações em 2 tentativas
        (4, 2, 2, True, True),    # 50% de reprovação e já aprovado em algum quiz
        (5, 2, 3, False, True),   # 40% < 50%: deixou de ter dificuldade
        (1, 1, 0, False, False),  # uma reprovação só não caracteriza dificuldade
    ],
)
def test_difficulty_rule(tentativas, reprovacoes, aprovacoes, dificuldade, aprovado):
    row = _desempenho_curso(
        {
            "usuario_id": 1,
            "curso_id": 2,
            "tentativas": tentativas,
            "reprovacoes": reprovacoes,
            "aprovacoes": aprovacoes,
            "nota_media": "55.456",
            "nota_maxima": None,
        }
    )
    assert row["tem_dificuldade"] is dificuldade
    assert row["aprovado"] is aprovado
    assert row["nota_media"] == 55.46


def test_difficulty_edge_is_removed_when_rule_stops_holding():
    step = next(s for s in EDGE_STEPS if s.name == "desempenho_cursos")
    assert "DELETE old_d" in step.merge
    assert "DELETE old_a" in step.merge


@pytest.mark.asyncio
async def test_recommendations_are_recomputed_and_stale_ones_removed():
    neo = FakeNeo4j()
    stats = await _populator(FakePostgres(), neo).run("full")
    vizinhos = neo.queries("'vizinhos_aprovados'")[0]
    reforco = neo.queries("'reforco'")[0]
    limpeza = neo.queries("r.run_id <> $run_id")[0]
    # mesma execução: a limpeza só remove o que NÃO foi reconfirmado agora
    assert vizinhos[1]["run_id"] == reforco[1]["run_id"] == limpeza[1]["run_id"]
    assert neo.calls.index(reforco) > neo.calls.index(vizinhos)  # reforço prevalece
    assert neo.calls.index(limpeza) > neo.calls.index(reforco)
    assert vizinhos[1]["min_vizinhos"] == etl.RECOMENDACAO_MIN_VIZINHOS
    assert stats["recomendacoes_removidas"] == 3


# ------------------------------------------------------ SQL x schema real


def _schema_columns() -> dict[str, set[str]]:
    text = SCHEMA.read_text(encoding="utf-8")
    result: dict[str, set[str]] = {}
    for match in re.finditer(r"CREATE TABLE\s+(tb_[a-z0-9_]+)\s*\((.*?)\)\s*;", text, re.I | re.S):
        columns = set()
        for line in match.group(2).splitlines():
            column = re.match(r"\s*([a-z_][a-z0-9_]*)\s+", line, re.I)
            if column and column.group(1).upper() not in {"CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK"}:
                columns.add(column.group(1).lower())
        result[match.group(1).lower()] = columns
    return result


def _all_sql(step: Step) -> list[str]:
    return [sql for sql in (step.full_sql, step.incremental_sql) if sql]


def test_etl_sql_only_references_existing_columns():
    columns = _schema_columns()
    problems = []
    for step in (*NODE_STEPS, *EDGE_STEPS):
        for sql in _all_sql(step):
            aliases = {
                alias.lower(): table.lower()
                for table, alias in re.findall(r"\b(?:FROM|JOIN)\s+(tb_[a-z0-9_]+)\s+([a-z][a-z0-9_]*)", sql, re.I)
            }
            for alias, column in re.findall(r"\b([a-z][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\b", sql, re.I):
                table = aliases.get(alias.lower())
                if table and table in columns and column.lower() not in columns[table]:
                    problems.append((step.name, table, column))
    assert problems == []


# ------------------------------------------------------------ agendamento


@pytest.mark.asyncio
async def test_loop_runs_full_first_then_incremental_and_survives_failures():
    modes = []

    async def runner(mode):
        modes.append(mode)
        if len(modes) == 2:
            raise RuntimeError("falha transitória")
        return {"modo": mode}

    async def no_sleep(_):
        return None

    await run_forever(900, 24, runner=runner, sleep=no_sleep, iterations=3)
    assert modes == ["full", "incremental", "incremental"]
