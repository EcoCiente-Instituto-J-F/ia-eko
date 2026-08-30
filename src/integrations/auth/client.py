from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger("ecociente.integrations.auth")


class AuthApiError(RuntimeError):
    """Erro base da integração de autenticação."""


class AuthApiUnavailable(AuthApiError):
    """A API de identidade não pôde ser consultada com segurança."""


class AuthApiUnauthorized(AuthApiError):
    """O token foi rejeitado pela API de identidade."""


class AuthApiProtocolError(AuthApiError):
    """A API de identidade respondeu fora do contrato esperado."""


class AuthApiClient:
    """Cliente assíncrono para um endpoint externo de validação de token.

    ``base_url`` deve ser o endpoint completo que recebe
    ``Authorization: Bearer <token>`` e devolve um objeto JSON com os claims.
    O token nunca é registrado em logs nem incluído nas exceções.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 8.0,
        retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.retries = max(0, retries)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            transport=transport,
            follow_redirects=False,
        )

    async def validate_token(self, token: str) -> dict[str, Any]:
        """Consulta os claims do token e retorna apenas um objeto JSON válido."""
        if not token or not token.strip():
            raise AuthApiUnauthorized("Token ausente ou vazio.")

        for attempt in range(self.retries + 1):
            try:
                response = await self._client.get(
                    self.base_url,
                    headers={"Authorization": f"Bearer {token.strip()}", "Accept": "application/json"},
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt < self.retries:
                    await self._wait_before_retry(attempt)
                    continue
                logger.warning("auth_api_unavailable", extra={"attempts": attempt + 1})
                raise AuthApiUnavailable("A API de autenticação está indisponível.") from exc

            if response.status_code in {401, 403}:
                raise AuthApiUnauthorized("Token inválido ou expirado.")
            if response.status_code >= 500:
                if attempt < self.retries:
                    await self._wait_before_retry(attempt)
                    continue
                logger.warning(
                    "auth_api_server_error",
                    extra={"status_code": response.status_code, "attempts": attempt + 1},
                )
                raise AuthApiUnavailable("A API de autenticação está indisponível.")
            if response.status_code >= 400:
                logger.warning("auth_api_unexpected_response", extra={"status_code": response.status_code})
                raise AuthApiProtocolError("A API de autenticação respondeu com erro inesperado.")

            try:
                payload = response.json()
            except ValueError as exc:
                raise AuthApiProtocolError("A API de autenticação não retornou JSON válido.") from exc
            if not isinstance(payload, dict):
                raise AuthApiProtocolError("A API de autenticação não retornou um objeto de claims.")
            return payload

        raise AuthApiUnavailable("A API de autenticação está indisponível.")

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    async def _wait_before_retry(attempt: int) -> None:
        # Backoff curto para uma operação idempotente (GET), sem bloquear o loop.
        await asyncio.sleep(0.1 * (attempt + 1))
