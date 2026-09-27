"""Tracing de LLM (Langfuse ou LangSmith) com mascaramento de PII.

Prometheus continua respondendo "quanto/quão rápido" (métricas agregadas).
Este módulo responde "o que aconteceu nesta conversa": cada chamada de
agente vira um span com prompt, resposta, tokens e latência, agrupados por
sessão e usuário.

Regras do projeto aplicadas aqui:
- **Opcional**: `TRACING_PROVIDER=none` (padrão) não importa nem instancia nada;
  testes e o modo `mock` nunca dependem de Langfuse/LangSmith.
- **PII nunca sai mascarada pela metade**: tudo que vai para o provedor passa
  por `mask_pii` (CPF, CNPJ, e-mail, telefone, CEP, JWT/Bearer e chaves
  sensíveis como `token`/`senha`). O schema do EcoCiente guarda CPF, e-mail e
  endereço; os traces carregam a mensagem integral do usuário.
- **Falha de telemetria nunca derruba o chat**: erro ao montar o provedor
  vira log de aviso e tracing desligado.
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

from src.core.config import Settings
from src.observability.metrics import LLM_TOKENS

logger = logging.getLogger("ecociente.tracing")

# --------------------------------------------------------------------------- #
# Mascaramento de PII
# --------------------------------------------------------------------------- #

# Padrões FORMATADOS são mascarados sempre. Sequências de dígitos soltas só
# quando passam na validação (dígito verificador de CPF/CNPJ, ou celular com
# DDD) — senão timestamps, IDs e contagens virariam "[TELEFONE]"/"[CPF]" e o
# trace perderia valor de depuração.
_PII_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Ordem importa: tokens e documentos formatados antes de telefone/CEP.
    (re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+\b"), "[JWT]"),
    (re.compile(r"(?i)\bbearer\s+[\w\-.~+/]+=*"), "Bearer [TOKEN]"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[EMAIL]"),
    (re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b"), "[CNPJ]"),
    (re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"), "[CPF]"),
    (re.compile(r"\b\d{2}\.\d{3}\.\d{3}-[\dxX]\b"), "[RG]"),
    (re.compile(r"(?:\+55\s?)?\(\d{2}\)\s?9?\d{4}[-\s]?\d{4}\b"), "[TELEFONE]"),
    (re.compile(r"\+55\s?\d{2}\s?9?\d{4}[-\s]?\d{4}\b"), "[TELEFONE]"),
    (re.compile(r"\b\d{2}[\s-]9?\d{4}-\d{4}\b"), "[TELEFONE]"),
    (re.compile(r"\b9\d{4}-\d{4}\b"), "[TELEFONE]"),
    (re.compile(r"\b\d{5}-\d{3}\b"), "[CEP]"),
)
_BARE_DIGITS = re.compile(r"\b\d{11}\b|\b\d{14}\b")


def _cpf_valido(digits: str) -> bool:
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    for size in (9, 10):
        total = sum(int(d) * w for d, w in zip(digits[:size], range(size + 1, 1, -1)))
        check = (total * 10) % 11 % 10
        if check != int(digits[size]):
            return False
    return True


def _cnpj_valido(digits: str) -> bool:
    if len(digits) != 14 or len(set(digits)) == 1:
        return False
    weights = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    for size in (12, 13):
        w = weights if size == 12 else [6, *weights]
        total = sum(int(d) * p for d, p in zip(digits[:size], w))
        rest = total % 11
        check = 0 if rest < 2 else 11 - rest
        if check != int(digits[size]):
            return False
    return True


def _mask_bare(match: re.Match[str]) -> str:
    digits = match.group(0)
    if len(digits) == 14:
        return "[CNPJ]" if _cnpj_valido(digits) else digits
    if _cpf_valido(digits):
        return "[CPF]"
    # Celular sem formatação: DDD (11-99) + 9 + 8 dígitos.
    if digits[0] != "0" and digits[1] != "0" and digits[2] == "9":
        return "[TELEFONE]"
    return digits

_SENSITIVE_KEYS = {
    "token",
    "authorization",
    "senha",
    "senha_hash",
    "password",
    "cpf",
    "cnpj",
    "email",
    "email_usuario",
    "telefone",
    "api_key",
    "secret",
}


def mask_text(text: str) -> str:
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return _BARE_DIGITS.sub(_mask_bare, text)


def mask_pii(data: Any, *, _depth: int = 0) -> Any:
    """Mascara PII recursivamente em str/dict/list/tuple; demais tipos passam intactos."""
    if _depth > 20:
        return data
    if isinstance(data, str):
        return mask_text(data)
    if isinstance(data, dict):
        masked: dict[Any, Any] = {}
        for key, value in data.items():
            if isinstance(key, str) and key.strip().lower() in _SENSITIVE_KEYS:
                masked[key] = "[REDACTED]"
            else:
                masked[key] = mask_pii(value, _depth=_depth + 1)
        return masked
    if isinstance(data, (list, tuple)):
        items = [mask_pii(item, _depth=_depth + 1) for item in data]
        return type(data)(items) if isinstance(data, tuple) else items
    return data


# --------------------------------------------------------------------------- #
# Contexto da conversa (sessão, usuário, consumo de tokens)
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(slots=True)
class ConversationTrace:
    session_id: str
    user_id: int
    request_id: str
    perfil: str
    usage: TokenUsage = field(default_factory=TokenUsage)


_current: ContextVar[ConversationTrace | None] = ContextVar("ecociente_conversation_trace", default=None)


@contextmanager
def conversation_trace(*, session_id: str, user_id: int, request_id: str, perfil: str) -> Iterator[ConversationTrace]:
    """Define o contexto da conversa atual. Nós do LangGraph rodam em tasks que
    copiam o contexto, e como `usage` é um objeto mutável compartilhado, o
    consumo de todos os agentes é somado no mesmo acumulador."""
    trace = ConversationTrace(session_id=session_id, user_id=user_id, request_id=request_id, perfil=perfil)
    token = _current.set(trace)
    try:
        yield trace
    finally:
        try:
            _current.reset(token)
        except ValueError:
            # Gerador do SSE finalizado em outro contexto (cliente desconectou):
            # o contexto original já não existe, nada a restaurar.
            pass


def current_trace() -> ConversationTrace | None:
    return _current.get()


def _usage_from_message(message: Any) -> tuple[int, int]:
    usage = getattr(message, "usage_metadata", None)
    if isinstance(usage, dict):
        return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    meta = getattr(message, "response_metadata", None) or {}
    token_usage = meta.get("token_usage") or meta.get("usage") or {}
    if isinstance(token_usage, dict):
        return (
            int(token_usage.get("prompt_tokens") or token_usage.get("input_tokens") or 0),
            int(token_usage.get("completion_tokens") or token_usage.get("output_tokens") or 0),
        )
    # Ollama devolve contagens próprias no response_metadata.
    return int(meta.get("prompt_eval_count") or 0), int(meta.get("eval_count") or 0)


def record_usage(*, provider: str, agent: str, messages: list[Any]) -> TokenUsage:
    """Soma o consumo das mensagens de IA, alimenta Prometheus e o acumulador da conversa."""
    usage = TokenUsage()
    for message in messages:
        if getattr(message, "type", None) not in {"ai", "AIMessageChunk"}:
            continue
        input_tokens, output_tokens = _usage_from_message(message)
        usage.input_tokens += input_tokens
        usage.output_tokens += output_tokens
    if usage.input_tokens:
        LLM_TOKENS.labels(provider=provider, agent=agent, kind="input").inc(usage.input_tokens)
    if usage.output_tokens:
        LLM_TOKENS.labels(provider=provider, agent=agent, kind="output").inc(usage.output_tokens)
    trace = current_trace()
    if trace is not None:
        trace.usage.input_tokens += usage.input_tokens
        trace.usage.output_tokens += usage.output_tokens
    return usage


# --------------------------------------------------------------------------- #
# Provedores
# --------------------------------------------------------------------------- #


class TracingService:
    """Fábrica de callbacks LangChain para o provedor configurado."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.provider = (settings.tracing_provider or "none").strip().lower()
        self._client: Any = None
        self._handler_factory: Any = None
        if settings.llm_provider == "mock" and not settings.tracing_in_mock:
            self.provider = "none"
        try:
            if self.provider == "langfuse":
                self._setup_langfuse()
            elif self.provider == "langsmith":
                self._setup_langsmith()
            elif self.provider != "none":
                logger.warning("tracing_provider_desconhecido", extra={"provider": self.provider})
                self.provider = "none"
        except Exception:  # telemetria nunca derruba a aplicação
            logger.warning("tracing_desabilitado_por_erro", exc_info=True)
            self.provider = "none"
            self._client = None
            self._handler_factory = None

    @property
    def enabled(self) -> bool:
        return self.provider != "none"

    def _setup_langfuse(self) -> None:
        if not (self.settings.langfuse_public_key and self.settings.langfuse_secret_key):
            raise RuntimeError("LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY ausentes.")
        from langfuse import Langfuse
        from langfuse.langchain import CallbackHandler

        mask = None
        if self.settings.tracing_mask_pii:

            def mask(*, data: Any, **_: Any) -> Any:
                return mask_pii(data)

        self._client = Langfuse(
            public_key=self.settings.langfuse_public_key,
            secret_key=self.settings.langfuse_secret_key,
            host=self.settings.langfuse_host,
            environment=self.settings.environment,
            release=self.settings.app_version,
            mask=mask,
        )
        public_key = self.settings.langfuse_public_key
        self._handler_factory = lambda: CallbackHandler(public_key=public_key)

    def _setup_langsmith(self) -> None:
        if not self.settings.langsmith_api_key:
            raise RuntimeError("LANGSMITH_API_KEY ausente.")
        from langchain_core.tracers import LangChainTracer
        from langsmith import Client

        hide = mask_pii if self.settings.tracing_mask_pii else None
        self._client = Client(
            api_key=self.settings.langsmith_api_key,
            hide_inputs=hide,
            hide_outputs=hide,
        )
        project = self.settings.langsmith_project
        client = self._client
        self._handler_factory = lambda: LangChainTracer(project_name=project, client=client)

    def run_config(self, agent_name: str) -> dict[str, Any]:
        """Config para `ainvoke(..., config=...)` com callbacks, tags e metadados de sessão."""
        if not self.enabled or self._handler_factory is None:
            return {}
        trace = current_trace()
        metadata: dict[str, Any] = {"agent": agent_name}
        tags = [f"agent:{agent_name}", f"env:{self.settings.environment}"]
        if trace is not None:
            tags.append(f"perfil:{trace.perfil}")
            metadata.update(
                {
                    "request_id": trace.request_id,
                    "perfil": trace.perfil,
                    # Chaves reconhecidas pelo CallbackHandler do Langfuse para
                    # agrupar spans por sessão/usuário.
                    "langfuse_session_id": trace.session_id,
                    "langfuse_user_id": str(trace.user_id),
                    "langfuse_tags": tags,
                    "session_id": trace.session_id,
                }
            )
        try:
            handler = self._handler_factory()
        except Exception:
            logger.warning("tracing_handler_falhou", exc_info=True)
            return {}
        return {
            "callbacks": [handler],
            "tags": tags,
            "metadata": metadata,
            "run_name": f"ecociente_{agent_name}",
        }

    def flush(self) -> None:
        client = self._client
        if client is None:
            return
        try:
            if hasattr(client, "flush"):
                client.flush()
        except Exception:
            logger.warning("tracing_flush_falhou", exc_info=True)

    def shutdown(self) -> None:
        client = self._client
        if client is None:
            return
        try:
            if hasattr(client, "shutdown"):
                client.shutdown()
            elif hasattr(client, "flush"):
                client.flush()
        except Exception:
            logger.warning("tracing_shutdown_falhou", exc_info=True)
