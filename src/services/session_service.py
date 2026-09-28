from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, AsyncIterator, Protocol

from src.core.config import Settings

if TYPE_CHECKING:
    from src.database.mongodb import MongoDatabase
    from src.database.redis import RedisDatabase
from src.observability.metrics import DB_LATENCY
from src.services.session_lock import SessionBusy, SessionLockManager

__all__ = ["SessionBusy", "SessionService"]

logger = logging.getLogger("ecociente.sessions")

CONVERSATION_ROLES = {"user", "assistant"}
COMPACTION_CAS_RETRIES = 3


class SessionError(RuntimeError):
    pass


class SessionNotFound(SessionError):
    pass


class SessionForbidden(SessionError):
    pass


class StorageUnavailable(SessionError):
    pass


class MemoryCompactionError(SessionError):
    pass


class MemorySummarizer(Protocol):
    async def summarize(self, previous_summary: str | None, messages: list[dict[str, Any]]) -> str: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _storage_error_types() -> tuple[type[BaseException], ...]:
    errors: list[type[BaseException]] = [TimeoutError, OSError]
    try:
        from pymongo.errors import PyMongoError

        errors.append(PyMongoError)
    except ImportError:
        pass
    try:
        from redis.exceptions import RedisError

        errors.append(RedisError)
    except ImportError:
        pass
    return tuple(errors)


def count_conversation_messages(messages: list[dict[str, Any]]) -> int:
    """Conta somente mensagens reais da conversa (user/assistant)."""
    return sum(1 for message in messages if str(message.get("role", "")).lower() in CONVERSATION_ROLES)


def _conversation_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(message)
        for message in messages
        if str(message.get("role", "")).lower() in CONVERSATION_ROLES
    ]


class SessionService:
    """Gerencia sessão e memória conversacional persistente sem clientes por request."""

    def __init__(self, settings: Settings, mongo: MongoDatabase, redis: RedisDatabase):
        self.settings = settings
        self.mongo = mongo
        self.redis_db = redis
        self._memory_sessions: dict[str, dict[str, Any]] = {}
        self._compaction_locks: dict[str, asyncio.Lock] = {}
        self.mode = settings.storage_mode
        # Redis só entra no lock quando as sessões estão no Mongo/Redis; com
        # sessões em memória não existe outra réplica com a mesma sessão.
        self.locks = SessionLockManager(
            redis_getter=lambda: self.redis if self.mode != "memory" else None,
            ttl_seconds=settings.session_lock_ttl_seconds,
            wait_seconds=settings.session_lock_wait_seconds,
        )

    @property
    def db(self):
        return self.mongo.database

    @property
    def redis(self):
        return self.redis_db.client

    async def start(self) -> None:
        if self.mode == "memory":
            return
        try:
            await self.mongo.start(self.settings)
            await self.redis_db.start(self.settings)
            await self.mongo.ensure_indexes()
        except _storage_error_types() as exc:
            if not self.settings.allow_storage_fallback:
                await self.mongo.close()
                await self.redis_db.close()
                raise StorageUnavailable("MongoDB/Redis indisponível na inicialização") from exc
            logger.warning("storage_fallback_to_memory", exc_info=exc)
            self.mode = "memory"
            await self.mongo.close()
            await self.redis_db.close()

    async def close(self) -> None:
        # Os clientes pertencem ao lifespan da aplicação e são fechados lá.
        return None

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
            "memory_summary": "",
            "memory_revision": 0,
            "messages": [],
        }

    async def create_session(self, usuario_id: int) -> dict[str, Any]:
        doc = self._new_document(usuario_id)
        if self.mode == "memory":
            self._memory_sessions[doc["session_id"]] = doc
            return dict(doc)
        if self.db is None or self.redis is None:
            raise StorageUnavailable("Persistência de sessão não inicializada.")
        started = time.perf_counter()
        await self.db.sessoes.insert_one(dict(doc))
        DB_LATENCY.labels(backend="mongodb", operation="create_session").observe(time.perf_counter() - started)
        started = time.perf_counter()
        await self.redis.set(f"session:ptr:{usuario_id}", doc["session_id"], ex=self.settings.session_ttl_seconds)
        DB_LATENCY.labels(backend="redis", operation="set_session_pointer").observe(time.perf_counter() - started)
        return doc

    async def get_session(self, session_id: str, usuario_id: int) -> dict[str, Any]:
        if self.mode == "memory":
            doc = self._memory_sessions.get(session_id)
        else:
            if self.db is None:
                raise StorageUnavailable("MongoDB não inicializado.")
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
        return await self.create_session(usuario_id) if session_id is None else await self.get_session(session_id, usuario_id)

    @asynccontextmanager
    async def conversation_guard(self, session_id: str) -> AsyncIterator[None]:
        """Serializa mensagens da mesma sessão, inclusive entre réplicas
        (Redis). Levanta SessionBusy se a sessão não liberar a tempo."""
        async with self.locks.hold(session_id):
            yield

    async def append_message(self, session_id: str, usuario_id: int, role: str, content: str) -> None:
        role = role.lower().strip()
        if role not in CONVERSATION_ROLES:
            raise ValueError("A memória conversacional aceita somente roles user/assistant.")
        await self.get_session(session_id, usuario_id)
        now = _now()
        msg = {"role": role, "content": content[:12000], "created_at": now}
        if self.mode == "memory":
            doc = self._memory_sessions[session_id]
            doc["messages"].append(msg)
            doc["updated_at"] = now
            doc["expira_em"] = now + timedelta(seconds=self.settings.session_ttl_seconds)
            return
        if self.db is None or self.redis is None:
            raise StorageUnavailable("Persistência de sessão não inicializada.")
        started = time.perf_counter()
        await self.db.sessoes.update_one(
            {"session_id": session_id, "usuario_id": usuario_id, "status": "active"},
            {
                "$push": {"messages": msg},
                "$set": {"updated_at": now, "expira_em": now + timedelta(seconds=self.settings.session_ttl_seconds)},
            },
        )
        DB_LATENCY.labels(backend="mongodb", operation="append_message").observe(time.perf_counter() - started)
        await self.redis.set(f"session:ptr:{usuario_id}", session_id, ex=self.settings.session_ttl_seconds)

    async def get_memory_context(
        self,
        session_id: str,
        usuario_id: int,
        *,
        current_user_message: str | None = None,
    ) -> dict[str, Any]:
        session = await self.get_session(session_id, usuario_id)
        conversation = _conversation_messages(session.get("messages", []))
        visible = conversation
        # A mensagem atual já é persistida antes do LangGraph. Ela é removida
        # apenas do bloco de histórico porque será enviada separadamente ao agente.
        if (
            current_user_message is not None
            and visible
            and visible[-1].get("role") == "user"
            and visible[-1].get("content") == current_user_message[:12000]
        ):
            visible = visible[:-1]
        return {
            "memory_summary": session.get("memory_summary", session.get("resumo_parcial", "")),
            "recent_messages": visible,
            "conversation_message_count": count_conversation_messages(conversation),
        }

    def needs_compaction(self, memory_context: dict[str, Any]) -> bool:
        return int(memory_context.get("conversation_message_count", 0)) > self.settings.memory_max_messages

    async def compact_memory_if_needed(
        self,
        session_id: str,
        usuario_id: int,
        summarizer: MemorySummarizer,
    ) -> bool:
        """Compacta deterministicamente quando user/assistant ultrapassam o limite.

        Em MongoDB, a gravação usa revisão otimista para impedir que duas
        compactações concorrentes sobrescrevam um resumo mais novo.
        """
        lock = self._compaction_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            for _attempt in range(COMPACTION_CAS_RETRIES):
                session = await self.get_session(session_id, usuario_id)
                conversation = _conversation_messages(session.get("messages", []))
                if len(conversation) <= self.settings.memory_max_messages:
                    return False

                keep = self.settings.memory_keep_recent_messages
                to_compact = conversation[:-keep]
                recent = conversation[-keep:]
                previous_summary = str(session.get("memory_summary", session.get("resumo_parcial", "")) or "").strip()

                try:
                    new_summary = (await summarizer.summarize(previous_summary or None, to_compact)).strip()
                except Exception:
                    logger.exception(
                        "memory_summarizer_failed",
                        extra={"session_id": session_id, "usuario_id": usuario_id},
                    )
                    raise
                if not new_summary:
                    raise MemoryCompactionError("O resumidor retornou um resumo vazio; histórico preservado.")

                now = _now()
                if self.mode == "memory":
                    doc = self._memory_sessions[session_id]
                    # O lock de compactação garante que o snapshot ainda é atual neste processo.
                    doc["memory_summary"] = new_summary
                    doc["messages"] = recent
                    doc["memory_revision"] = int(doc.get("memory_revision", 0)) + 1
                    doc["updated_at"] = now
                    doc["expira_em"] = now + timedelta(seconds=self.settings.session_ttl_seconds)
                    return True

                if self.db is None:
                    raise StorageUnavailable("MongoDB não inicializado.")
                revision = int(session.get("memory_revision", 0))
                query: dict[str, Any] = {
                    "session_id": session_id,
                    "usuario_id": usuario_id,
                    "status": "active",
                }
                if "memory_revision" in session:
                    query["memory_revision"] = revision
                else:
                    query["$or"] = [
                        {"memory_revision": {"$exists": False}},
                        {"memory_revision": 0},
                    ]
                started = time.perf_counter()
                result = await self.db.sessoes.update_one(
                    query,
                    {
                        "$set": {
                            "memory_summary": new_summary,
                            "messages": recent,
                            "updated_at": now,
                            "expira_em": now + timedelta(seconds=self.settings.session_ttl_seconds),
                        },
                        "$inc": {"memory_revision": 1},
                        "$unset": {"resumo_parcial": ""},
                    },
                )
                DB_LATENCY.labels(backend="mongodb", operation="compact_memory").observe(time.perf_counter() - started)
                if result.matched_count == 1:
                    return True
                logger.warning(
                    "memory_compaction_revision_conflict",
                    extra={"session_id": session_id, "usuario_id": usuario_id, "revision": revision},
                )

            raise MemoryCompactionError("Não foi possível persistir a compactação após conflitos concorrentes.")

    async def update_last_route(self, session_id: str, usuario_id: int, route: str) -> None:
        await self.get_session(session_id, usuario_id)
        now = _now()
        if self.mode == "memory":
            doc = self._memory_sessions[session_id]
            doc["ultima_rota"] = route
            doc["updated_at"] = now
            return
        if self.db is None:
            raise StorageUnavailable("MongoDB não inicializado.")
        started = time.perf_counter()
        await self.db.sessoes.update_one(
            {"session_id": session_id, "usuario_id": usuario_id, "status": "active"},
            {"$set": {"ultima_rota": route, "updated_at": now}},
        )
        DB_LATENCY.labels(backend="mongodb", operation="update_session_route").observe(time.perf_counter() - started)

    async def close_session(self, session_id: str, usuario_id: int) -> dict[str, Any]:
        await self.get_session(session_id, usuario_id)
        if self.mode == "memory":
            self._memory_sessions.pop(session_id, None)
            self._compaction_locks.pop(session_id, None)
            return {"session_id": session_id, "status": "closed"}
        if self.db is None or self.redis is None:
            raise StorageUnavailable("Persistência de sessão não inicializada.")
        await self.db.sessoes.delete_one({"session_id": session_id, "usuario_id": usuario_id})
        pointer_key = f"session:ptr:{usuario_id}"
        if await self.redis.get(pointer_key) == session_id:
            await self.redis.delete(pointer_key)
        self._compaction_locks.pop(session_id, None)
        return {"session_id": session_id, "status": "closed"}

    async def health(self) -> dict[str, str]:
        if self.mode == "memory":
            return {"mongodb": "memory_fallback", "redis": "memory_fallback"}
        result = {"mongodb": "error", "redis": "error"}
        try:
            result["mongodb"] = "ok" if await asyncio.wait_for(self.mongo.ping(), timeout=1.5) else "error"
        except _storage_error_types():
            result["mongodb"] = "error"
        try:
            result["redis"] = "ok" if await asyncio.wait_for(self.redis_db.ping(), timeout=1.5) else "error"
        except _storage_error_types():
            result["redis"] = "error"
        return result
