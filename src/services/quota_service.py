"""Limites de uso do chat: cota diária por perfil, anti-rajada e teto por sessão.

Três regras independentes, checadas antes de qualquer chamada de LLM:

1. **Cota diária por perfil** — respostas do `/chat` por usuário por dia
   (virada à meia-noite de `QUOTA_TIMEZONE`, padrão America/Sao_Paulo).
2. **Anti-rajada** — requisições por minuto por usuário, igual para todos os
   perfis. Protege contra script/loop, não contra uso normal.
3. **Teto por sessão sem resumo** — para os perfis em
   `QUOTA_NO_COMPACTION_PROFILES` (padrão: `USUARIO_COMUM`), a sessão é
   encerrada antes de a memória passar de `MEMORY_MAX_MESSAGES`. Assim o
   resumidor de memória (uma chamada extra de LLM) nunca é acionado para
   esse perfil. Com os padrões (20 mensagens), são 10 perguntas por sessão.

Contadores ficam no Redis (`INCR` atômico + `EXPIRE`), com fallback em
memória quando o storage roda em modo `memory` (testes/dev). A cota é
reservada antes de invocar o grafo e devolvida se a execução falhar, para o
usuário não perder uma resposta por erro do servidor.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from src.core.config import Settings
from src.observability.metrics import QUOTA_REJECTED
from src.security.roles import (
    COOPERATIVA,
    MORADOR_RESIDENCIAL,
    SINDICO_COMERCIAL,
    SINDICO_RESIDENCIAL,
    USUARIO_COMERCIAL,
    USUARIO_COMUM,
    normalize_profile,
)
from src.services.session_service import count_conversation_messages
from src.shared.context import UserContext

if TYPE_CHECKING:
    from src.database.redis import RedisDatabase

logger = logging.getLogger("ecociente.quota")

_DAY_TTL_SECONDS = 60 * 60 * 36  # sobra para fuso/virada; a chave já é por data
_MINUTE_TTL_SECONDS = 90


@dataclass(frozen=True, slots=True)
class QuotaStatus:
    limit: int | None
    used: int
    reset_at: datetime
    # Chave efetivamente incrementada. O reembolso usa ESTA chave: recalcular
    # o dia no momento do reembolso erraria o dia se a requisição cruzasse a
    # meia-noite (e criaria um contador negativo no dia seguinte).
    day_key: str | None = None

    @property
    def remaining(self) -> int | None:
        return None if self.limit is None else max(self.limit - self.used, 0)


class QuotaExceeded(RuntimeError):
    """Limite atingido. `kind` ∈ {"daily", "rate", "session"}."""

    def __init__(self, kind: str, message: str, *, limit: int | None = None, retry_after: int | None = None, reset_at: datetime | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.limit = limit
        self.retry_after = retry_after
        self.reset_at = reset_at


class QuotaService:
    def __init__(self, settings: Settings, redis: "RedisDatabase | None" = None):
        self.settings = settings
        self.redis_db = redis
        self._memory: dict[str, tuple[int, float]] = {}
        self._tz = ZoneInfo(settings.quota_timezone)
        # Injetáveis para teste (janela de minuto e virada de dia determinísticas).
        self._clock = time.time
        self._now_fn = lambda: datetime.now(self._tz)
        self._no_compaction = {
            profile
            for raw in settings.quota_no_compaction_profiles.split(",")
            if (profile := normalize_profile(raw.strip())) is not None
        }

    # ------------------------------------------------------------------ #
    # Configuração
    # ------------------------------------------------------------------ #

    def daily_limit(self, perfil: str | None) -> int | None:
        profile = normalize_profile(perfil)
        limits = {
            USUARIO_COMUM: self.settings.quota_usuario_comum,
            MORADOR_RESIDENCIAL: self.settings.quota_morador_residencial,
            USUARIO_COMERCIAL: self.settings.quota_usuario_comercial,
            COOPERATIVA: self.settings.quota_cooperativa,
            SINDICO_RESIDENCIAL: self.settings.quota_sindico_residencial,
            SINDICO_COMERCIAL: self.settings.quota_sindico_comercial,
        }
        # Perfil desconhecido recebe o limite mais restritivo, nunca "ilimitado".
        value = limits.get(profile, self.settings.quota_usuario_comum) if profile else self.settings.quota_usuario_comum
        return value if value > 0 else None

    def session_message_cap(self, perfil: str | None) -> int | None:
        """Máximo de mensagens (user+assistant) antes de o resumo ser disparado."""
        if normalize_profile(perfil) not in self._no_compaction:
            return None
        return self.settings.memory_max_messages

    # ------------------------------------------------------------------ #
    # Tempo
    # ------------------------------------------------------------------ #

    def _now(self) -> datetime:
        return self._now_fn()

    def _day_key(self, user_id: int, now: datetime) -> str:
        return f"quota:daily:{user_id}:{now.date().isoformat()}"

    def _next_midnight(self, now: datetime) -> datetime:
        tomorrow = (now + timedelta(days=1)).date()
        return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=self._tz)

    # ------------------------------------------------------------------ #
    # Contadores (Redis com fallback em memória)
    # ------------------------------------------------------------------ #

    @property
    def _redis(self) -> Any:
        return None if self.redis_db is None else self.redis_db.client

    async def _incr(self, key: str, ttl: int) -> int:
        client = self._redis
        if client is not None:
            try:
                value = int(await client.incr(key))
            except Exception:
                logger.warning("quota_redis_falhou_usando_memoria", exc_info=True)
            else:
                if value == 1:
                    # Só a primeira escrita define o TTL (compatível com Redis < 7,
                    # que não suporta EXPIRE ... NX). Falha aqui não pode cair no
                    # contador em memória: o INCR já contou no Redis.
                    try:
                        await client.expire(key, ttl)
                    except Exception:
                        logger.warning("quota_redis_expire_falhou", exc_info=True)
                return value
        now = time.monotonic()
        value, expires = self._memory.get(key, (0, now + ttl))
        if expires <= now:
            value, expires = 0, now + ttl
        value += 1
        self._memory[key] = (value, expires)
        return value

    async def _decr(self, key: str) -> None:
        client = self._redis
        if client is not None:
            try:
                await client.decr(key)
                return
            except Exception:
                logger.warning("quota_redis_decr_falhou", exc_info=True)
        if key in self._memory:
            value, expires = self._memory[key]
            self._memory[key] = (max(value - 1, 0), expires)

    async def _get(self, key: str) -> int:
        client = self._redis
        if client is not None:
            try:
                return int(await client.get(key) or 0)
            except Exception:
                logger.warning("quota_redis_get_falhou", exc_info=True)
        value, expires = self._memory.get(key, (0, 0.0))
        return value if expires > time.monotonic() else 0

    # ------------------------------------------------------------------ #
    # API pública
    # ------------------------------------------------------------------ #

    async def status(self, user: UserContext) -> QuotaStatus:
        now = self._now()
        used = await self._get(self._day_key(user.user_id, now))
        return QuotaStatus(limit=self.daily_limit(user.perfil), used=used, reset_at=self._next_midnight(now))

    async def check_rate(self, user: UserContext) -> None:
        if not self.settings.quota_enabled or self.settings.rate_limit_per_minute <= 0:
            return
        now = self._clock()
        window = int(now // 60)
        key = f"quota:rate:{user.user_id}:{window}"
        count = await self._incr(key, _MINUTE_TTL_SECONDS)
        if count > self.settings.rate_limit_per_minute:
            QUOTA_REJECTED.labels(perfil=normalize_profile(user.perfil) or "desconhecido", kind="rate").inc()
            retry_after = max(1, 60 - int(now % 60))
            raise QuotaExceeded(
                "rate",
                "Muitas mensagens em sequência. Aguarde alguns segundos e tente novamente.",
                limit=self.settings.rate_limit_per_minute,
                retry_after=retry_after,
            )

    def check_session(self, user: UserContext, session: dict[str, Any]) -> None:
        cap = self.session_message_cap(user.perfil)
        if cap is None or not self.settings.quota_enabled:
            return
        existing = count_conversation_messages(session.get("messages", []))
        # +1 = a mensagem que o usuário está enviando agora. Se ela fizesse a
        # contagem passar de MEMORY_MAX_MESSAGES, o grafo acionaria o resumidor.
        if existing + 1 > cap:
            QUOTA_REJECTED.labels(perfil=normalize_profile(user.perfil) or "desconhecido", kind="session").inc()
            raise QuotaExceeded(
                "session",
                "Esta conversa chegou ao limite de mensagens. Inicie uma nova conversa para continuar.",
                limit=cap // 2,
            )

    async def reserve_daily(self, user: UserContext) -> QuotaStatus:
        """Consome 1 da cota diária de forma atômica; lança QuotaExceeded se estourar."""
        now = self._now()
        limit = self.daily_limit(user.perfil)
        reset_at = self._next_midnight(now)
        if not self.settings.quota_enabled or limit is None:
            return QuotaStatus(limit=None, used=0, reset_at=reset_at)
        key = self._day_key(user.user_id, now)
        used = await self._incr(key, _DAY_TTL_SECONDS)
        if used > limit:
            await self._decr(key)
            QUOTA_REJECTED.labels(perfil=normalize_profile(user.perfil) or "desconhecido", kind="daily").inc()
            raise QuotaExceeded(
                "daily",
                f"Você atingiu o limite de {limit} respostas por dia. O limite renova à meia-noite.",
                limit=limit,
                retry_after=int((reset_at - now).total_seconds()),
                reset_at=reset_at,
            )
        return QuotaStatus(limit=limit, used=used, reset_at=reset_at, day_key=key)

    async def refund_daily(self, status: QuotaStatus) -> None:
        """Devolve a reserva feita por `reserve_daily` (mesma chave/dia)."""
        if status.day_key is None:
            return
        await self._decr(status.day_key)
