"""ETL do grafo contra um PostgreSQL de verdade com o schema do projeto.

Os outros testes do ETL usam um Postgres falso; aqui o SQL de cada passo roda
no `sql/ecociente_schema.sql` real, com dados gerados por
`tests/fixtures/etl_seed.sql`. O lado Neo4j é um gravador que confere se
todo parâmetro é serializável pelo driver (Decimal, por exemplo, não é).

Postgres: binários locais (initdb/pg_ctl) numa pasta temporária; sem eles,
TEST_POSTGRES_URL; sem nenhum dos dois, os testes são pulados.
"""

from __future__ import annotations

import datetime as dt
import glob
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest

import src.etl.populate_graph as etl
from src.core.config import Settings
from src.database.postgres import PostgresDatabase
from src.etl.validate_graph import CONTAGENS

pytestmark = pytest.mark.asyncio

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "sql" / "ecociente_schema.sql"
SEED = ROOT / "tests" / "fixtures" / "etl_seed.sql"
VOLUME = dict(usuarios=600, condos=6, coops=4, cursos=6, tentativas=4000, postagens=1500, votos=4000)
BOLT_OK = (type(None), bool, int, float, str, dt.datetime, dt.date)


def _pg_bin(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    candidates = sorted(glob.glob(f"/usr/lib/postgresql/*/bin/{name}"))
    return candidates[-1] if candidates else None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _psql(url: str, *args: str) -> None:
    subprocess.run([_pg_bin("psql") or "psql", url, "-q", "-v", "ON_ERROR_STOP=1", *args], check=True, capture_output=True)


@pytest.fixture(scope="module")
def pg_url():
    url = os.getenv("TEST_POSTGRES_URL")
    initdb, pg_ctl, psql = _pg_bin("initdb"), _pg_bin("pg_ctl"), _pg_bin("psql")
    proc_dir = None
    if not url:
        if not (initdb and pg_ctl and psql):
            pytest.skip("PostgreSQL local indisponível e TEST_POSTGRES_URL não definida")
        # Fora de /tmp/pytest-of-<user> (0700): o usuário postgres precisa entrar.
        proc_dir = Path(tempfile.mkdtemp(prefix="ecociente_pg_"))
        data = proc_dir / "data"
        # initdb recusa rodar como root; nesse caso usa o usuário postgres.
        prefix = ["su", "postgres", "-c"] if os.geteuid() == 0 else None
        if prefix:
            os.chmod(proc_dir, 0o777)
        port = _free_port()

        def run(cmd: str) -> None:
            if prefix:
                subprocess.run([*prefix, cmd], check=True, capture_output=True)
            else:
                subprocess.run(cmd, shell=True, check=True, capture_output=True)

        run(f"{initdb} -D {data} -A trust -U postgres")
        run(f"{pg_ctl} -D {data} -o '-p {port} -k {proc_dir}' -l {proc_dir}/pg.log -w start")
        base = f"postgresql://postgres@127.0.0.1:{port}"
        subprocess.run([psql, f"{base}/postgres", "-qc", "CREATE DATABASE etl_test"], check=True, capture_output=True)
        url = f"{base}/etl_test"
    _psql(url, "-f", str(SCHEMA))
    _psql(url, *[f"-v{k}={v}" for k, v in VOLUME.items()], "-f", str(SEED))
    # O seed acabou de rodar: joga tudo para "ontem" para que a janela do
    # incremental só veja o que cada teste fizer.
    _psql(
        url,
        "-c",
        "UPDATE tb_log_auditoria SET executado_em = executado_em - interval '1 day';"
        "UPDATE tb_usuarios SET registro_em = registro_em - interval '1 day';",
    )
    yield url
    if proc_dir is not None:
        cmd = f"{pg_ctl} -D {proc_dir / 'data'} -m fast stop"
        if os.geteuid() == 0:
            subprocess.run(["su", "postgres", "-c", cmd], capture_output=True)
        else:
            subprocess.run(cmd, shell=True, capture_output=True)
        shutil.rmtree(proc_dir, ignore_errors=True)


@pytest.fixture()
def postgres(pg_url):
    db = PostgresDatabase()
    db.start(Settings.from_env().with_overrides(postgres_url=pg_url, postgres_pool_min=1, postgres_pool_max=2))
    yield db
    db.close()


def _bolt_safe(value, path="$"):
    if isinstance(value, dict):
        for key, item in value.items():
            _bolt_safe(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _bolt_safe(item, f"{path}[{index}]")
    elif not isinstance(value, BOLT_OK):
        raise AssertionError(f"{path}: {type(value).__name__} não é aceito pelo driver Neo4j")


class RecordingNeo4j:
    def __init__(self):
        self.checkpoint: dt.datetime | None = None
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, query, parameters=None):
        parameters = parameters or {}
        _bolt_safe(parameters)
        self.calls.append((query, parameters))
        if "RETURN c.last_success_at" in query:
            return [{"last": self.checkpoint.isoformat()}] if self.checkpoint else []
        if "SET c.last_success_at" in query:
            self.checkpoint = dt.datetime.fromisoformat(parameters["at"])
        return [{"total": 0}] if "RETURN count" in query else []

    def rows_of(self, step_name: str) -> list[dict]:
        step = next(s for s in (*etl.NODE_STEPS, *etl.EDGE_STEPS) if s.name == step_name)
        rows: list[dict] = []
        for query, params in self.calls:
            if step.merge in query:
                rows.extend(params["rows"])
        return rows

    def reset_rows(self) -> None:
        self.calls = []


def _populator(postgres, neo):
    populator = etl.GraphPopulator()
    populator.postgres = postgres
    populator.neo4j = neo
    return populator


def _expected(postgres, key: str) -> int:
    sql = CONTAGENS[key][0]
    sql = sql.replace("%(min_rep)s", str(etl.DIFICULDADE_MIN_REPROVACOES)).replace(
        "%(min_taxa)s", repr(float(etl.DIFICULDADE_MIN_TAXA))
    )
    return int(postgres.fetch_one(sql)["n"])


async def test_full_run_sends_exactly_what_the_validation_expects(postgres) -> None:
    """Cruza o SQL/transform de cada passo com o SQL independente do --validar."""
    neo = RecordingNeo4j()
    stats = await _populator(postgres, neo).run("full")

    assert stats["usuarios"] == _expected(postgres, "Usuario") == VOLUME["usuarios"]
    assert stats["postagens"] == _expected(postgres, "Postagem")
    assert stats["mora_em"] == _expected(postgres, "MORA_EM")
    ativos = [r for r in neo.rows_of("pertence_a_usuario_condominio") if r["ativo"]]
    assert len(ativos) == _expected(postgres, "PERTENCE_A (usuário)")
    assert stats["validou"] == _expected(postgres, "VALIDOU")
    assert stats["denunciou"] == _expected(postgres, "DENUNCIOU")
    desempenho = neo.rows_of("desempenho_cursos")
    assert sum(r["aprovado"] for r in desempenho) == _expected(postgres, "APROVADO_EM")
    assert sum(r["tem_dificuldade"] for r in desempenho) == _expected(postgres, "TEM_DIFICULDADE_EM")
    assert isinstance(ativos[0]["trust_score"], float)  # numeric do Postgres vira float
    assert "aprovado" not in ativos[0] and "data_saida" not in ativos[0]
    assert set(stats["duracao_s"]) >= {"usuarios", "validou", "recomendacoes", "total"}
    # Lotes do tamanho configurado.
    assert max(len(p["rows"]) for q, p in neo.calls if "UNWIND $rows" in q) <= etl._BATCH_SIZE


async def test_incremental_catches_recent_activity_through_dates_and_audit(postgres, pg_url) -> None:
    neo = RecordingNeo4j()
    populator = _populator(postgres, neo)
    await populator.run("full")
    neo.checkpoint = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)  # "última execução"
    neo.reset_rows()

    _psql(
        pg_url,
        "-c",
        """
        INSERT INTO tb_enderecos (cidade) VALUES ('SP');
        INSERT INTO tb_usuarios (nome_usuario, email_usuario, senha_hash, tipo_usuario_id, endereco_id)
          VALUES ('Nova', 'nova@ex.com', 'x', 2, (SELECT max(id_endereco) FROM tb_enderecos));
        -- saiu do condomínio (UPDATE auditado) e mudou o trust_score de outro vínculo
        UPDATE tb_rel_usuarios_condominios SET data_saida = now() WHERE id_usuario_condominio = 1;
        UPDATE tb_rel_usuarios_condominios SET trust_score = 99 WHERE id_usuario_condominio = 2;
        UPDATE tb_postagens SET status_validacao_id = 2, resolvido_em = now() WHERE id_postagem = 3;
        -- categoria corrigida na moderação, sem resolvido_em: só a auditoria vê
        UPDATE tb_postagens SET categoria_id = 1 + (categoria_id % 8) WHERE id_postagem = 4;
        INSERT INTO tb_tentativas_quiz (usuario_id, quiz_id, nota, aprovado, iniciado_em, concluido_em)
          VALUES (5, 1, 20, false, now(), now());
        """,
    )
    stats = await populator.run("incremental")

    assert stats["modo"] == "incremental"
    assert stats["usuarios"] == 1
    vinculos = {r["vinculo_id"]: r for r in neo.rows_of("pertence_a_usuario_condominio")}
    assert set(vinculos) == {1, 2}  # só os tocados, não a tabela inteira
    assert vinculos[1]["ativo"] is False and vinculos[2]["trust_score"] == 99.0
    assert stats["postagens"] == 2 and stats["postagem_relacoes"] == 2
    assert {r["postagem_id"] for r in neo.rows_of("postagem_relacoes")} == {3, 4}
    assert stats["desempenho_cursos"] == 1
    # MORA_EM: só moradores dos vínculos alterados (e usuários novos), não todos.
    assert 0 < stats["mora_em"] <= 2


async def test_postgres_schema_blocks_deletes_so_etl_does_not_rely_on_them(pg_url) -> None:
    """Achado da validação: a auditoria referencia a linha original por FK sem
    ON DELETE, então postagens e vínculos não podem ser apagados. Se o schema
    mudar, este teste avisa que o ETL precisa propagar DELETE."""
    with pytest.raises(subprocess.CalledProcessError) as exc:
        _psql(
            pg_url,
            "-c",
            "DELETE FROM tb_postagens WHERE id_postagem = (SELECT max(id_postagem) FROM tb_postagens p "
            "WHERE NOT EXISTS (SELECT 1 FROM tb_rel_votos_postagens v WHERE v.postagem_id = p.id_postagem))",
        )
    assert b"tb_log_auditoria_postagens" in exc.value.stderr


async def test_iter_batches_streams_with_server_side_cursor(postgres) -> None:
    lotes = list(postgres.iter_batches("SELECT id_usuario FROM tb_usuarios ORDER BY 1", (), 250))
    assert [len(lote) for lote in lotes[:2]] == [250, 250]
    assert sum(len(lote) for lote in lotes) == postgres.fetch_one("SELECT count(*) AS n FROM tb_usuarios")["n"]
    # A conexão volta ao pool utilizável (autocommit restaurado por connection()).
    assert postgres.ping()
    # Consumidor que para no meio não quebra o pool.
    gen = postgres.iter_batches("SELECT id_usuario FROM tb_usuarios", (), 10)
    next(gen)
    gen.close()
    assert postgres.ping()


async def test_prune_computes_orphans_in_python(postgres) -> None:
    class GraphWithLeftovers(RecordingNeo4j):
        async def execute(self, query, parameters=None):
            parameters = parameters or {}
            if query.startswith("MATCH (n:Postagem)") and "ORDER BY n.id" in query:
                return [] if parameters.get("after") else [{"id": 1}, {"id": 2}, {"id": 999999}]
            if "MATCH (u:Usuario)" in query and "collect(DISTINCT pc.id)" in query:
                if parameters.get("after"):
                    return []
                row = postgres.fetch_one(
                    "SELECT usuario_id, condominio_id FROM tb_moradores ORDER BY usuario_id LIMIT 1"
                )
                return [{"id": row["usuario_id"], "pertence": [row["condominio_id"], 424242], "mora": [424242]}]
            return await super().execute(query, parameters)

    neo = GraphWithLeftovers()
    removidos = await _populator(postgres, neo)._prune()
    assert removidos["postagens_removidas"] == 1
    assert removidos["mora_em_removidas"] == 1
    assert removidos["pertence_a_removidas"] >= 1
    deletes = [p["rows"] for q, p in neo.calls if "DETACH DELETE p" in q]
    assert deletes == [[999999]]
    assert not any("NOT p.id IN" in q for q, _ in neo.calls)
