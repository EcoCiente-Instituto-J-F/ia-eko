from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from redis.asyncio import Redis
from pymongo import AsyncMongoClient

from src.core.config import Settings
from prometheus_client import Histogram

DB_LATENCY = Histogram(
    "ecociente_db_duration_seconds", "Latência de banco/cache", ["backend", "operation"]
)

class SessionError(RuntimeError):
    pass


class SessionNotFound(SessionError):
    pass


class SessionForbidden(SessionError):
    pass


class StorageUnavailable(SessionError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SessionService:
    """Sessão bruta/curta no Mongo, ponteiro/cache no Redis e memória consolidada no Mongo.

    O modo `memory` existe exclusivamente para testes/ambiente acadêmico sem infraestrutura.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._memory_sessions: dict[str, dict[str, Any]] = {}
        self._memory_long_term: dict[int, dict[str, Any]] = {}
        self.mongo_client: AsyncMongoClient | None = None
        self.redis: Redis | None = None
        self.db = None
        self.mode = settings.storage_mode

    async def start(self) -> None:
        if self.mode == "memory":
            return
        self.mongo_client = AsyncMongoClient(
            self.settings.mongodb_uri,
            serverSelectionTimeoutMS=1500,
            connectTimeoutMS=1500,
        )
        self.redis = Redis.from_url(self.settings.redis_url, decode_responses=True)
        self.db = self.mongo_client[self.settings.mongodb_database]
        try:
            await asyncio.wait_for(self.mongo_client.admin.command("ping"), timeout=2)
            await asyncio.wait_for(self.redis.ping(), timeout=2)
            await self.db.sessoes.create_index("session_id", unique=True)
            await self.db.sessoes.create_index("expira_em", expireAfterSeconds=0)
            await self.db.memoria_longo_prazo.create_index("usuario_id", unique=True)
        except Exception as exc:
            if not self.settings.allow_storage_fallback:
                raise StorageUnavailable("MongoDB/Redis indisponível na inicialização") from exc
            self.mode = "memory"
            await self.close_external()

    async def close_external(self) -> None:
        if self.redis is not None:
            await self.redis.aclose()
            self.redis = None
        if self.mongo_client is not None:
            await self.mongo_client.close()
            self.mongo_client = None
        self.db = None

    async def close(self) -> None:
        await self.close_external()

    def _new_document(self, usuario_id: int) -> dict[str, Any]:
        now = _now()
        return {
            "session_id": str(uuid.uuid4()),
            "usuario_id": usuario_id,
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "expira_em": now + timedelta(seconds=self.settings.session_ttl_seconds),
            "ultima_rota": None,
            "resumo_parcial": "",
            "messages": [],
        }

    async def create_session(self, usuario_id: int) -> dict[str, Any]:
        doc = self._new_document(usuario_id)
        if self.mode == "memory":
            self._memory_sessions[doc["session_id"]] = doc
            return dict(doc)
        assert self.db is not None and self.redis is not None
        started = time.perf_counter()
        await self.db.sessoes.insert_one(dict(doc))
        DB_LATENCY.labels(backend="mongodb", operation="create_session").observe(time.perf_counter() - started)
        started = time.perf_counter()
        await self.redis.set(
            f"session:ptr:{usuario_id}", doc["session_id"], ex=self.settings.session_ttl_seconds
        )
        DB_LATENCY.labels(backend="redis", operation="set_session_pointer").observe(time.perf_counter() - started)
        return doc

    async def get_session(self, session_id: str, usuario_id: int) -> dict[str, Any]:
        if self.mode == "memory":
            doc = self._memory_sessions.get(session_id)
        else:
            assert self.db is not None
            started = time.perf_counter()
            doc = await self.db.sessoes.find_one({"session_id": session_id}, {"_id": 0})
            DB_LATENCY.labels(backend="mongodb", operation="get_session").observe(time.perf_counter() - started)
        if not doc:
            raise SessionNotFound("Sessão não encontrada ou expirada.")
        if int(doc["usuario_id"]) != int(usuario_id):
            raise SessionForbidden("A sessão pertence a outro usuário.")
        if doc.get("status") != "active":
            raise SessionNotFound("Sessão encerrada.")
        return dict(doc)

    async def resolve_session(self, usuario_id: int, session_id: str | None) -> dict[str, Any]:
        # Regra acadêmica: session_id ausente SEMPRE cria uma conversa nova.
        if session_id is None:
            return await self.create_session(usuario_id)
        return await self.get_session(session_id, usuario_id)

    async def append_message(self, session_id: str, usuario_id: int, role: str, content: str) -> None:
        await self.get_session(session_id, usuario_id)
        now = _now()
        msg = {"role": role, "content": content[:12000], "created_at": now}
        if self.mode == "memory":
            doc = self._memory_sessions[session_id]
            doc["messages"].append(msg)
            doc["messages"] = doc["messages"][-12:]
            doc["updated_at"] = now
            doc["expira_em"] = now + timedelta(seconds=self.settings.session_ttl_seconds)
            return
        assert self.db is not None and self.redis is not None
        started = time.perf_counter()
        await self.db.sessoes.update_one(
            {"session_id": session_id, "usuario_id": usuario_id},
            {
                "$push": {"messages": {"$each": [msg], "$slice": -12}},
                "$set": {
                    "updated_at": now,
                    "expira_em": now + timedelta(seconds=self.settings.session_ttl_seconds),
                },
            },
        )
        DB_LATENCY.labels(backend="mongodb", operation="append_message").observe(time.perf_counter() - started)
        await self.redis.set(
            f"session:ptr:{usuario_id}", session_id, ex=self.settings.session_ttl_seconds
        )

    async def get_memory_context(self, session_id: str, usuario_id: int) -> dict[str, Any]:
        session = await self.get_session(session_id, usuario_id)
        if self.mode == "memory":
            long_term = self._memory_long_term.get(usuario_id, {})
        else:
            assert self.db is not None
            started = time.perf_counter()
            long_term = await self.db.memoria_longo_prazo.find_one(
                {"usuario_id": usuario_id}, {"_id": 0}
            ) or {}
            DB_LATENCY.labels(backend="mongodb", operation="get_long_term_memory").observe(time.perf_counter() - started)
        return {
            "resumo_parcial": session.get("resumo_parcial", ""),
            "ultima_rota": session.get("ultima_rota"),
            "mensagens_recentes": session.get("messages", [])[-6:],
            "memoria_longo_prazo": long_term.get("resumo_consolidado", ""),
        }

    async def update_route_and_summary(
        self, session_id: str, usuario_id: int, route: str, summary: str
    ) -> None:
        summary = summary[:3000]
        if self.mode == "memory":
            doc = self._memory_sessions[session_id]
            doc["ultima_rota"] = route
            doc["resumo_parcial"] = summary
            doc["updated_at"] = _now()
            return
        assert self.db is not None
        started = time.perf_counter()
        await self.db.sessoes.update_one(
            {"session_id": session_id, "usuario_id": usuario_id},
            {"$set": {"ultima_rota": route, "resumo_parcial": summary, "updated_at": _now()}},
        )
        DB_LATENCY.labels(backend="mongodb", operation="update_session_summary").observe(time.perf_counter() - started)


    async def consolidate_long_term(self, usuario_id: int, summary: str) -> None:
        """Persiste somente um resumo compacto útil; nunca o histórico bruto completo."""
        summary = summary.strip()[:3000]
        if not summary:
            return
        now = _now()
        if self.mode == "memory":
            self._memory_long_term[usuario_id] = {
                "usuario_id": usuario_id,
                "resumo_consolidado": summary,
                "updated_at": now,
            }
            return
        assert self.db is not None
        started = time.perf_counter()
        await self.db.memoria_longo_prazo.update_one(
            {"usuario_id": usuario_id},
            {
                "$set": {"resumo_consolidado": summary, "updated_at": now},
                "$setOnInsert": {"usuario_id": usuario_id, "created_at": now},
            },
            upsert=True,
        )
        DB_LATENCY.labels(backend="mongodb", operation="consolidate_long_term").observe(time.perf_counter() - started)

    async def close_session(self, session_id: str, usuario_id: int) -> dict[str, Any]:
        session = await self.get_session(session_id, usuario_id)
        compact = session.get("resumo_parcial", "").strip()
        if not compact:
            compact = "Sessão encerrada sem resumo consolidado relevante."
        now = _now()
        if self.mode == "memory":
            self._memory_long_term[usuario_id] = {
                "usuario_id": usuario_id,
                "resumo_consolidado": compact[:3000],
                "updated_at": now,
            }
            self._memory_sessions.pop(session_id, None)
            return {"session_id": session_id, "status": "closed"}
        assert self.db is not None and self.redis is not None
        await self.db.memoria_longo_prazo.update_one(
            {"usuario_id": usuario_id},
            {
                "$set": {
                    "resumo_consolidado": compact[:3000],
                    "updated_at": now,
                },
                "$setOnInsert": {"usuario_id": usuario_id, "created_at": now},
            },
            upsert=True,
        )
        await self.db.sessoes.delete_one({"session_id": session_id, "usuario_id": usuario_id})
        current = await self.redis.get(f"session:ptr:{usuario_id}")
        if current == session_id:
            await self.redis.delete(f"session:ptr:{usuario_id}")
        return {"session_id": session_id, "status": "closed"}

    async def health(self) -> dict[str, str]:
        if self.mode == "memory":
            return {"mongodb": "memory_fallback", "redis": "memory_fallback"}
        assert self.mongo_client is not None and self.redis is not None
        result: dict[str, str] = {}
        try:
            await asyncio.wait_for(self.mongo_client.admin.command("ping"), timeout=1)
            result["mongodb"] = "ok"
        except Exception:
            result["mongodb"] = "error"
        try:
            await asyncio.wait_for(self.redis.ping(), timeout=1)
            result["redis"] = "ok"
        except Exception:
            result["redis"] = "error"
        return result
