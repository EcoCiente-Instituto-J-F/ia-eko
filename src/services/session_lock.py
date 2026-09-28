"""Lock por sessão de conversa, válido entre réplicas da API.

Duas mensagens da mesma sessão não podem ser processadas ao mesmo tempo: a
ordem da memória (append → grafo → append da resposta) quebraria, e o teto de
mensagens do perfil USUARIO_COMUM poderia ser furado e disparar o resumo de
memória (a chamada extra de LLM que o teto existe para evitar).

Como funciona:

1. Um `asyncio.Lock` local por sessão enfileira as requisições do MESMO
   processo, para que só uma delas por vez dispute o Redis.
2. No Redis, `SET session:lock:<id> <token> NX PX <ttl>` garante exclusão
   entre processos/pods. O token é aleatório por aquisição.
3. Enquanto a mensagem é processada, uma tarefa renova o TTL a cada ttl/3,
   só se o valor ainda for o token dela (script Lua, atômico). Assim uma
   resposta lenta do LLM não perde o lock, e um pod que morre libera a sessão
   sozinho em no máximo `SESSION_LOCK_TTL_SECONDS`.
4. A liberação também é compare-and-delete em Lua: nunca apaga o lock que já
   expirou e foi adquirido por outra réplica.

Sem Redis (STORAGE_MODE=memory ou fallback) só existe o lock local — com as
sessões em memória não há como haver outra réplica servindo a mesma sessão.
Se o Redis falhar no meio da operação, a mensagem segue só com o lock local
(fail-open) e o evento é registrado; a alternativa seria derrubar o chat
inteiro por causa de uma proteção de concorrência.
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable

from src.observability.metrics import SESSION_LOCK_EVENTS, SESSION_LOCK_WAIT

logger = logging.getLogger("ecociente.session_lock")

LOCK_KEY_PREFIX = "session:lock:"

# KEYS[1]=chave, ARGV[1]=token. Só apaga se o lock ainda for nosso.
_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""

# KEYS[1]=chave, ARGV[1]=token, ARGV[2]=ttl em ms. Só renova se for nosso.
_RENEW = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""


class SessionBusy(RuntimeError):
    """Outra mensagem desta sessão ainda está em processamento."""

    def __init__(self, session_id: str, waited_seconds: float):
        super().__init__(
            "Outra mensagem desta conversa ainda está sendo respondida. Aguarde a resposta e tente de novo."
        )
        self.session_id = session_id
        self.waited_seconds = waited_seconds


def _redis_errors() -> tuple[type[BaseException], ...]:
    errors: list[type[BaseException]] = [OSError, TimeoutError, ConnectionError]
    try:
        from redis.exceptions import RedisError

        errors.append(RedisError)
    except ImportError:  # pragma: no cover - redis é dependência base
        pass
    return tuple(errors)


class _AcquireTimeout(Exception):
    """Prazo de espera pelo lock esgotado (interno; vira SessionBusy)."""


class _LocalLocks:
    """asyncio.Lock por chave, removido quando ninguém mais o usa (o dict
    antigo crescia a cada sessão criada e só encolhia no close_session)."""

    def __init__(self) -> None:
        self._entries: dict[str, list[Any]] = {}

    def __len__(self) -> int:
        return len(self._entries)

    @asynccontextmanager
    async def hold(self, key: str, timeout: float) -> AsyncIterator[None]:
        entry = self._entries.get(key)
        if entry is None:
            entry = [asyncio.Lock(), 0]
            self._entries[key] = entry
        entry[1] += 1
        lock: asyncio.Lock = entry[0]
        try:
            if timeout <= 0:
                # wait_for(timeout=0) cancela antes de tentar, mesmo com o lock
                # livre; prazo zero = "só se estiver livre agora".
                if lock.locked():
                    raise _AcquireTimeout
                await lock.acquire()
            else:
                try:
                    await asyncio.wait_for(lock.acquire(), timeout=timeout)
                except asyncio.TimeoutError:
                    raise _AcquireTimeout from None
            try:
                yield
            finally:
                lock.release()
        finally:
            entry[1] -= 1
            if entry[1] == 0 and self._entries.get(key) is entry:
                del self._entries[key]


class SessionLockManager:
    def __init__(
        self,
        *,
        redis_getter: Callable[[], Any],
        ttl_seconds: float = 30.0,
        wait_seconds: float = 45.0,
        key_prefix: str = LOCK_KEY_PREFIX,
    ):
        self._redis_getter = redis_getter
        self.ttl_ms = max(int(ttl_seconds * 1000), 1000)
        self.wait_seconds = max(float(wait_seconds), 0.0)
        self.key_prefix = key_prefix
        self.local = _LocalLocks()

    def key(self, session_id: str) -> str:
        return f"{self.key_prefix}{session_id}"

    @asynccontextmanager
    async def hold(self, session_id: str) -> AsyncIterator[None]:
        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + self.wait_seconds

        # Só os prazos de AQUISIÇÃO viram SessionBusy. Um TimeoutError vindo
        # do corpo (ex.: LLM lento) propaga como está.
        local = self.local.hold(session_id, self.wait_seconds)
        try:
            await local.__aenter__()
        except _AcquireTimeout:
            raise self._busy(session_id, loop.time() - started) from None
        try:
            redis = self._redis_getter()
            token = None
            if redis is None:
                SESSION_LOCK_WAIT.labels(backend="local").observe(loop.time() - started)
                SESSION_LOCK_EVENTS.labels(outcome="acquired_local").inc()
            else:
                try:
                    token = await self._acquire(redis, session_id, deadline, started)
                except _AcquireTimeout:
                    raise self._busy(session_id, loop.time() - started) from None

            if token is None:
                # Sem Redis, ou Redis falhou: só o lock local (fail-open).
                yield
                return

            renewer = asyncio.create_task(self._renew_loop(redis, session_id, token))
            try:
                yield
            finally:
                renewer.cancel()
                try:
                    # return_exceptions: o CancelledError da renovação vira
                    # resultado; um cancelamento da PRÓPRIA tarefa continua
                    # propagando (antes era engolido por `except BaseException`).
                    await asyncio.gather(renewer, return_exceptions=True)
                finally:
                    # shield: se a tarefa foi cancelada (cliente fechou o SSE),
                    # a liberação ainda precisa chegar ao Redis.
                    await asyncio.shield(self._release(redis, session_id, token))
        finally:
            await local.__aexit__(None, None, None)

    @staticmethod
    def _busy(session_id: str, waited: float) -> SessionBusy:
        SESSION_LOCK_EVENTS.labels(outcome="timeout").inc()
        return SessionBusy(session_id, waited)

    async def _acquire(self, redis: Any, session_id: str, deadline: float, started: float) -> str | None:
        loop = asyncio.get_running_loop()
        key = self.key(session_id)
        token = uuid.uuid4().hex
        delay = 0.05
        while True:
            try:
                acquired = await redis.set(key, token, nx=True, px=self.ttl_ms)
            except _redis_errors():
                logger.warning("session_lock_redis_error", extra={"session_id": session_id}, exc_info=True)
                SESSION_LOCK_EVENTS.labels(outcome="redis_error").inc()
                return None
            if acquired:
                SESSION_LOCK_WAIT.labels(backend="redis").observe(loop.time() - started)
                SESSION_LOCK_EVENTS.labels(outcome="acquired_redis").inc()
                return token
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise _AcquireTimeout
            # Backoff com jitter para réplicas não baterem no Redis em sincronia.
            await asyncio.sleep(min(delay * random.uniform(0.5, 1.5), remaining))
            delay = min(delay * 2, 0.5)

    async def _renew_loop(self, redis: Any, session_id: str, token: str) -> None:
        key = self.key(session_id)
        interval = self.ttl_ms / 3000
        while True:
            await asyncio.sleep(interval)
            try:
                renewed = await redis.eval(_RENEW, 1, key, token, self.ttl_ms)
            except _redis_errors():
                # Tenta de novo no próximo ciclo; o TTL ainda cobre ~2 ciclos.
                logger.warning("session_lock_renew_error", extra={"session_id": session_id}, exc_info=True)
                continue
            if not renewed:
                # O lock expirou (ex.: processo pausado por mais que o TTL) e
                # talvez outra réplica já o tenha. Não dá para abortar a
                # resposta em andamento com segurança; registra para alerta.
                SESSION_LOCK_EVENTS.labels(outcome="lost").inc()
                logger.error("session_lock_lost", extra={"session_id": session_id})
                return

    async def _release(self, redis: Any, session_id: str, token: str) -> None:
        try:
            await redis.eval(_RELEASE, 1, self.key(session_id), token)
        except _redis_errors():
            # O TTL libera a sessão sozinho.
            logger.warning("session_lock_release_error", extra={"session_id": session_id}, exc_info=True)

