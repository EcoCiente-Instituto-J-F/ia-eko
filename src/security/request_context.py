from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_bearer_token: ContextVar[str | None] = ContextVar("ecociente_request_bearer_token", default=None)


@contextmanager
def authenticated_request_context(token: str | None) -> Iterator[None]:
    """Mantém credencial apenas no contexto assíncrono da request.

    O valor não entra no estado LangGraph, memória conversacional, prompt ou logs.
    ContextVars são propagadas para tasks filhas criadas durante a request.
    """
    context_token = _bearer_token.set(token.strip() if token and token.strip() else None)
    try:
        yield
    finally:
        try:
            _bearer_token.reset(context_token)
        except ValueError:
            # Gerador SSE finalizado em outro Context (cliente desconectou e o
            # finalizador de async generators fechou o stream): o Context da
            # request já foi descartado junto com o valor.
            pass


def current_bearer_token() -> str | None:
    return _bearer_token.get()
