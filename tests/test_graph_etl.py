from __future__ import annotations

import pytest

from src.etl.populate_graph import GraphPopulator


class FakePostgres:
    def __init__(self, table_rows: dict[str, list[dict]]):
        self.table_rows = table_rows
        self.queries: list[str] = []

    def fetch_all(self, query: str, params=()):
        self.queries.append(query)
        for table, rows in self.table_rows.items():
            if table in query:
                return rows
        return []


class FakeNeo4j:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, query: str, parameters=None):
        self.calls.append((query, parameters or {}))
        return []


@pytest.mark.asyncio
async def test_load_mora_em_merges_edges_in_batches():
    populator = GraphPopulator()
    populator.postgres = FakePostgres(
        {"tb_moradores": [{"usuario_id": 1, "condominio_id": 10}, {"usuario_id": 2, "condominio_id": 10}]}
    )
    populator.neo4j = FakeNeo4j()

    total = await populator._load_mora_em()

    assert total == 2
    query, params = populator.neo4j.calls[0]
    assert "MERGE (u)-[:MORA_EM]->(c)" in query
    assert params["rows"] == [{"usuario_id": 1, "condominio_id": 10}, {"usuario_id": 2, "condominio_id": 10}]


@pytest.mark.asyncio
async def test_load_pertence_a_casts_trust_score_to_float():
    populator = GraphPopulator()
    populator.postgres = FakePostgres(
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
    populator.neo4j = FakeNeo4j()

    total = await populator._load_pertence_a()

    assert total == 1
    _, params = populator.neo4j.calls[0]
    assert isinstance(params["rows"][0]["trust_score"], float)


@pytest.mark.asyncio
async def test_ensure_constraints_creates_one_per_label():
    populator = GraphPopulator()
    populator.neo4j = FakeNeo4j()

    await populator._ensure_constraints()

    queries = [query for query, _ in populator.neo4j.calls]
    assert len(queries) == len(GraphPopulator._NODE_LABELS)
    for label in GraphPopulator._NODE_LABELS:
        assert any(
            f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE" in q
            for q in queries
        )


@pytest.mark.asyncio
async def test_run_batches_respects_batch_size(monkeypatch):
    populator = GraphPopulator()
    rows = [{"usuario_id": i, "condominio_id": 10} for i in range(1200)]
    populator.postgres = FakePostgres({"tb_moradores": rows})
    populator.neo4j = FakeNeo4j()
    monkeypatch.setattr("src.etl.populate_graph._BATCH_SIZE", 500)

    total = await populator._load_mora_em()

    assert total == 1200
    assert len(populator.neo4j.calls) == 3
