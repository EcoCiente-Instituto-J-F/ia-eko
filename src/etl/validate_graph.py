"""Validação do ETL do grafo contra os bancos reais (Postgres + Neo4j/Aura).

    python -m src.etl.populate_graph --validar [--relatorio etl_validacao.json]

Roda, na ordem:

1. ``full`` cronometrado por passo;
2. conferência de contagens: cada label/relação do grafo contra a consulta
   equivalente no Postgres (é aqui que aparece dado que não entrou);
3. ``full`` de novo: as contagens têm que ser idênticas (MERGE idempotente);
4. ``incremental`` cronometrado — é o que roda a cada 15 min no CronJob.

Grava no grafo exatamente o que a carga normal gravaria (sem ``--prune``).
O relatório mostra também o total de nós e relações, para comparar com o
limite do plano do Aura.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger("ecociente.etl.validate_graph")

# chave → (SQL no Postgres, Cypher no grafo). Mesma semântica das regras do ETL.
CONTAGENS: dict[str, tuple[str, str]] = {
    "Usuario": ("SELECT count(*) AS n FROM tb_usuarios", "MATCH (n:Usuario) RETURN count(n) AS n"),
    "Condominio": ("SELECT count(*) AS n FROM tb_condominios", "MATCH (n:Condominio) RETURN count(n) AS n"),
    "Torre": ("SELECT count(*) AS n FROM tb_torres", "MATCH (n:Torre) RETURN count(n) AS n"),
    "Cooperativa": ("SELECT count(*) AS n FROM tb_cooperativas", "MATCH (n:Cooperativa) RETURN count(n) AS n"),
    "CategoriaResiduo": (
        "SELECT count(*) AS n FROM tb_lkp_categorias_residuos",
        "MATCH (n:CategoriaResiduo) RETURN count(n) AS n",
    ),
    "Curso": ("SELECT count(*) AS n FROM tb_cursos", "MATCH (n:Curso) RETURN count(n) AS n"),
    "Postagem": ("SELECT count(*) AS n FROM tb_postagens", "MATCH (n:Postagem) RETURN count(n) AS n"),
    "MORA_EM": ("SELECT count(*) AS n FROM tb_moradores", "MATCH (:Usuario)-[r:MORA_EM]->(:Condominio) RETURN count(r) AS n"),
    "PERTENCE_A (usuário)": (
        "SELECT count(*) AS n FROM tb_rel_usuarios_condominios WHERE aprovado AND data_saida IS NULL",
        "MATCH (:Usuario)-[r:PERTENCE_A]->(:Condominio) RETURN count(r) AS n",
    ),
    "PERTENCE_A (torre)": ("SELECT count(*) AS n FROM tb_torres", "MATCH (:Torre)-[r:PERTENCE_A]->(:Condominio) RETURN count(r) AS n"),
    "REPRESENTADA_POR": (
        "SELECT count(*) AS n FROM tb_cooperativas WHERE usuario_id IS NOT NULL",
        "MATCH ()-[r:REPRESENTADA_POR]->() RETURN count(r) AS n",
    ),
    "CRIOU": ("SELECT count(*) AS n FROM tb_postagens", "MATCH ()-[r:CRIOU]->() RETURN count(r) AS n"),
    "NO_CONDOMINIO": ("SELECT count(*) AS n FROM tb_postagens", "MATCH ()-[r:NO_CONDOMINIO]->() RETURN count(r) AS n"),
    "DA_CATEGORIA": ("SELECT count(*) AS n FROM tb_postagens", "MATCH ()-[r:DA_CATEGORIA]->() RETURN count(r) AS n"),
    "NA_TORRE": (
        "SELECT count(*) AS n FROM tb_postagens WHERE torre_id IS NOT NULL",
        "MATCH ()-[r:NA_TORRE]->() RETURN count(r) AS n",
    ),
    "VALIDOU": (
        "SELECT count(*) AS n FROM tb_rel_votos_postagens WHERE motivo_denuncia_id IS NULL",
        "MATCH ()-[r:VALIDOU]->() RETURN count(r) AS n",
    ),
    "DENUNCIOU": (
        "SELECT count(*) AS n FROM tb_rel_votos_postagens WHERE motivo_denuncia_id IS NOT NULL",
        "MATCH ()-[r:DENUNCIOU]->() RETURN count(r) AS n",
    ),
    "APROVADO_EM": (
        """SELECT count(*) AS n FROM (
             SELECT t.usuario_id, q.curso_id FROM tb_tentativas_quiz t JOIN tb_quizzes q ON q.id_quiz = t.quiz_id
             WHERE t.concluido_em IS NOT NULL GROUP BY 1, 2 HAVING bool_or(t.aprovado)) x""",
        "MATCH ()-[r:APROVADO_EM]->() RETURN count(r) AS n",
    ),
    "TEM_DIFICULDADE_EM": (
        """SELECT count(*) AS n FROM (
             SELECT t.usuario_id, q.curso_id,
                    count(*) AS tentativas, sum(CASE WHEN t.aprovado THEN 0 ELSE 1 END) AS reprovacoes
             FROM tb_tentativas_quiz t JOIN tb_quizzes q ON q.id_quiz = t.quiz_id
             WHERE t.concluido_em IS NOT NULL GROUP BY 1, 2) x
           WHERE reprovacoes >= %(min_rep)s AND reprovacoes::float / tentativas >= %(min_taxa)s""",
        "MATCH ()-[r:TEM_DIFICULDADE_EM]->() RETURN count(r) AS n",
    ),
}

TOTAIS_GRAFO = {
    "nos": "MATCH (n) RETURN count(n) AS n",
    "relacoes": "MATCH ()-[r]->() RETURN count(r) AS n",
}


async def contar(populator: Any) -> dict[str, dict[str, Any]]:
    from src.etl.populate_graph import DIFICULDADE_MIN_REPROVACOES, DIFICULDADE_MIN_TAXA

    # Constantes do módulo (não é entrada de usuário): entram direto no SQL.
    substituir = {"%(min_rep)s": str(int(DIFICULDADE_MIN_REPROVACOES)), "%(min_taxa)s": repr(float(DIFICULDADE_MIN_TAXA))}
    resultado: dict[str, dict[str, Any]] = {}
    for chave, (sql, cypher) in CONTAGENS.items():
        for marcador, valor in substituir.items():
            sql = sql.replace(marcador, valor)
        row = populator.postgres.fetch_one(sql)
        esperado = int((row or {}).get("n", 0))
        rows = await populator.neo4j.execute(cypher)
        no_grafo = int(rows[0]["n"]) if rows else 0
        resultado[chave] = {"postgres": esperado, "grafo": no_grafo, "ok": esperado == no_grafo}
    return resultado


async def totais(populator: Any) -> dict[str, int]:
    saida = {}
    for chave, cypher in TOTAIS_GRAFO.items():
        rows = await populator.neo4j.execute(cypher)
        saida[chave] = int(rows[0]["n"]) if rows else 0
    return saida


async def validar(populator: Any) -> dict[str, Any]:
    relatorio: dict[str, Any] = {}

    t0 = time.perf_counter()
    full = await populator.run("full")
    relatorio["full_1"] = {"segundos": round(time.perf_counter() - t0, 2), "duracao_por_passo": full.get("duracao_s")}
    contagens = await contar(populator)
    relatorio["contagens"] = contagens
    relatorio["divergencias"] = {k: v for k, v in contagens.items() if not v["ok"]}

    t0 = time.perf_counter()
    await populator.run("full")
    relatorio["full_2"] = {"segundos": round(time.perf_counter() - t0, 2)}
    depois = await contar(populator)
    relatorio["idempotente"] = all(depois[k]["grafo"] == contagens[k]["grafo"] for k in contagens)

    t0 = time.perf_counter()
    inc = await populator.run("incremental")
    relatorio["incremental"] = {
        "segundos": round(time.perf_counter() - t0, 2),
        "linhas": {k: v for k, v in inc.items() if isinstance(v, int)},
        "duracao_por_passo": inc.get("duracao_s"),
    }
    relatorio["grafo_total"] = await totais(populator)
    relatorio["ok"] = not relatorio["divergencias"] and relatorio["idempotente"]
    return relatorio


def imprimir(relatorio: dict[str, Any], caminho: str | None = None) -> None:
    texto = json.dumps(relatorio, ensure_ascii=False, indent=2, default=str)
    if caminho:
        with open(caminho, "w", encoding="utf-8") as fh:
            fh.write(texto)
    print(texto)
