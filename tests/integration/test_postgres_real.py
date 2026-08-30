from __future__ import annotations

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from src.core.config import Settings

pytestmark = pytest.mark.integration


def test_real_postgres_schema_is_reachable(integration_settings: Settings) -> None:
    if not integration_settings.test_postgres_url:
        pytest.skip("TEST_POSTGRES_URL não configurado")
    conn = psycopg2.connect(integration_settings.test_postgres_url, connect_timeout=3)
    try:
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), to_regclass('public.tb_postagens')")
            database_name, table_name = cur.fetchone()
        assert database_name
        assert table_name == "tb_postagens"
    finally:
        conn.close()
