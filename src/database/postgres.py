from __future__ import annotations

from contextlib import contextmanager
from threading import Lock
from typing import Any, Iterator, Sequence

from psycopg2 import pool
from psycopg2.extras import RealDictCursor

from src.core.config import Settings


class PostgresUnavailable(RuntimeError):
    pass


class PostgresDatabase:
    """Pool PostgreSQL compartilhado pelo processo FastAPI e pelas tools Analytics."""

    def __init__(self) -> None:
        self._pool: pool.ThreadedConnectionPool | None = None
        self._lock = Lock()

    @property
    def configured(self) -> bool:
        return self._pool is not None

    def start(self, settings: Settings) -> None:
        if not settings.postgres_url:
            return
        with self._lock:
            if self._pool is not None:
                return
            self._pool = pool.ThreadedConnectionPool(
                minconn=max(1, settings.postgres_pool_min),
                maxconn=max(settings.postgres_pool_min, settings.postgres_pool_max),
                dsn=settings.postgres_url,
                connect_timeout=settings.postgres_connect_timeout,
                application_name="ecociente_ia",
            )

    def close(self) -> None:
        with self._lock:
            current, self._pool = self._pool, None
        if current is not None:
            current.closeall()

    @contextmanager
    def connection(self) -> Iterator[Any]:
        current = self._pool

        

        conn = current.getconn()

        try:
            conn.set_session(
                readonly=True,
                autocommit=True
            )

            yield conn

        except Exception:
            current.putconn(conn, close=True)
            raise



    def fetch_all(self, query: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self.connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, tuple(params))
            return [dict(row) for row in cur.fetchall()]

    def fetch_one(self, query: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        with self.connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, tuple(params))
            row = cur.fetchone()
            return dict(row) if row else None

    def ping(self) -> bool:
        row = self.fetch_one("SELECT 1 AS ok")
        return bool(row and row["ok"] == 1)


postgres_db = PostgresDatabase()
