"""Tool `recomendacoes_curso`: monta a resposta a partir de RECOMENDADO_PARA (Neo4j mockado)."""

import pytest

from src.agents.graph_traversal import tools as grafo


@pytest.mark.asyncio
async def test_recomendacoes_monta_lista_ordenada(monkeypatch):
    async def fake_execute(query, params):
        assert "RECOMENDADO_PARA" in query and params == {"usuario_id": 7, "limite": 2}
        return [
            {"curso_id": 3, "titulo": "Compostagem", "motivo": "vizinhos_aprovados", "score": 4.0},
            {"curso_id": 1, "titulo": "Reciclagem", "motivo": "reforco", "score": 2.0},
        ]

    monkeypatch.setattr(grafo.neo4j_db, "execute", fake_execute)
    out = await grafo.recomendacoes_curso.ainvoke({"usuario_id": 7, "limite": 2})
    assert out["status"] == "ok"
    assert [r["curso_id"] for r in out["recomendacoes"]] == [3, 1]


@pytest.mark.asyncio
async def test_recomendacoes_sem_neo4j_inicializado_devolve_indisponivel(monkeypatch):
    async def fake_execute(query, params):
        raise RuntimeError("não inicializado")

    monkeypatch.setattr(grafo.neo4j_db, "execute", fake_execute)
    out = await grafo.recomendacoes_curso.ainvoke({"usuario_id": 7})
    assert out["status"] == "indisponivel"
