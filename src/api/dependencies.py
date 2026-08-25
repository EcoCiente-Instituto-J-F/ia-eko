from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request, status

from src.agents.graph import EcoGraphRuntime
from src.core.config import Settings
from src.security.authentication import (
    AuthenticationService,
    AuthenticationUnavailable,
    InvalidToken,
    InvalidUserClaims,
)
from src.security.roles import normalize_profile
from src.services.chat_service import ChatService
from src.services.health_service import HealthService
from src.services.rag_service import RAGService
from src.services.ranking_service import RankingService
from src.services.session_service import SessionService
from src.shared.context import UserContext


async def get_user_context(
    request: Request,
    authorization: str | None = Header(None, alias="Authorization"),
    x_usuario_id: int | None = Header(None, alias="X-Usuario-Id", gt=0),
    x_perfil: str | None = Header(None, alias="X-Perfil"),
    x_condominio_id: int | None = Header(None, alias="X-Condominio-Id", gt=0),
    x_permissoes: str | None = Header(None, alias="X-Permissoes"),
) -> UserContext:
    """Resolve a identidade via API externa antes de qualquer agente.

    Em ambiente de teste/migração, headers legados só são aceitos quando
    ``ALLOW_LEGACY_IDENTITY_HEADERS=true``. Em produção o token é obrigatório.
    """
    settings = get_settings(request)
    if settings.allow_legacy_identity_headers and x_usuario_id is not None:
        profile = normalize_profile(x_perfil)
        if profile is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Perfil legado não reconhecido.")
        permissions = [item.strip() for item in (x_permissoes or "").split(",") if item.strip()]
        return UserContext(
            user_id=x_usuario_id,
            perfil=profile,
            condominio_id=x_condominio_id,
            permissoes=permissions,
        )

    token = _bearer_token(authorization) or await _body_token(request)
    if token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token de autenticação ausente.")

    service = get_authentication_service(request)
    try:
        return await service.authenticate(token)
    except InvalidToken as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido ou expirado.") from exc
    except (AuthenticationUnavailable, InvalidUserClaims) as exc:
        # Não revelamos a disponibilidade nem o contrato interno do provedor.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Não foi possível validar sua sessão neste momento. Tente novamente em instantes.",
        ) from exc


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization deve usar Bearer token.")
    return value.strip()


async def _body_token(request: Request) -> str | None:
    """Lê token do contrato ``POST /chat`` sem registrá-lo ou repassá-lo ao grafo."""
    try:
        body = await request.json()
    except Exception:
        return None
    token = body.get("token") if isinstance(body, dict) else None
    return token.strip() if isinstance(token, str) and token.strip() else None


def require_same_user(body_usuario_id: int | None, user: UserContext) -> None:
    if body_usuario_id is not None and body_usuario_id != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="usuario_id do corpo não corresponde ao usuário da requisição.",
        )


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_session_service(request: Request) -> SessionService:
    return request.app.state.sessions


def get_rag_service(request: Request) -> RAGService:
    return request.app.state.rag


def get_graph(request: Request) -> EcoGraphRuntime:
    return request.app.state.graph


def get_chat_service(request: Request) -> ChatService:
    return request.app.state.chat


def get_authentication_service(request: Request) -> AuthenticationService:
    return request.app.state.authentication


def get_ranking_service(request: Request) -> RankingService:
    return request.app.state.rankings


def get_health_service(request: Request) -> HealthService:
    return request.app.state.health_service
