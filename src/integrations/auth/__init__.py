"""Integração com a API externa de autenticação."""

from src.integrations.auth.client import (
    AuthApiClient,
    AuthApiError,
    AuthApiProtocolError,
    AuthApiUnauthorized,
    AuthApiUnavailable,
)

__all__ = [
    "AuthApiClient",
    "AuthApiError",
    "AuthApiProtocolError",
    "AuthApiUnauthorized",
    "AuthApiUnavailable",
]
