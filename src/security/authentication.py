from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from src.integrations.auth.client import (
    AuthApiClient,
    AuthApiProtocolError,
    AuthApiUnauthorized,
    AuthApiUnavailable,
)
from src.security.roles import (
    COOPERATIVA,
    MORADOR_RESIDENCIAL,
    SINDICO_COMERCIAL,
    SINDICO_RESIDENCIAL,
    normalize_profile,
)
from src.shared.context import UserContext


class AuthenticationError(RuntimeError):
    """Erro base de autenticação sem detalhes sensíveis para o cliente."""


class InvalidToken(AuthenticationError):
    pass


class AuthenticationUnavailable(AuthenticationError):
    pass


class InvalidUserClaims(AuthenticationError):
    pass


class AuthIdentity(BaseModel):
    """Contrato mínimo que a API externa deve devolver após validar o token."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    user_id: int = Field(validation_alias=AliasChoices("id", "user_id", "usuario_id"), gt=0)
    perfil: str = Field(validation_alias=AliasChoices("perfil", "profile", "tipoUsuario", "tipo_usuario"), min_length=1)
    condominio_id: int | None = Field(
        default=None,
        validation_alias=AliasChoices("condominio_id", "condominium_id"),
        gt=0,
    )
    permissoes: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("permissoes", "permissions"),
    )
    cooperativa_id: int | None = Field(
        default=None,
        validation_alias=AliasChoices("cooperativa_id", "cooperative_id"),
        gt=0,
    )

    @model_validator(mode="before")
    @classmethod
    def extrair_vinculos_do_perfil_mobile(cls, payload: Any) -> Any:
        """Adapta /api/usuarios/me da Mobile sem confiar em IDs enviados pelo chat.

        Usa apenas vínculos ativos/confirmados vindos da API autenticada. Se a
        conta tiver múltiplos condomínios, não escolhe um arbitrariamente.
        """
        if not isinstance(payload, dict):
            return payload
        data = dict(payload)
        perfil = normalize_profile(str(
            data.get("perfil") or data.get("profile") or data.get("tipoUsuario")
            or data.get("tipo_usuario") or ""
        ))

        if data.get("condominio_id") is None and data.get("condominium_id") is None:
            vinculos = data.get("condominios")
            candidatos: set[int] = set()
            if isinstance(vinculos, list):
                for condominio in vinculos:
                    if not isinstance(condominio, dict):
                        continue
                    cid = condominio.get("id")
                    if type(cid) is not int or cid <= 0 or condominio.get("ativo") is False:
                        continue
                    if perfil in {SINDICO_RESIDENCIAL, SINDICO_COMERCIAL}:
                        autorizado = condominio.get("sindicoResponsavel") is True
                    elif perfil == MORADOR_RESIDENCIAL:
                        vinculo = condominio.get("vinculo")
                        autorizado = (
                            isinstance(vinculo, dict)
                            and vinculo.get("aprovado") is True
                            and vinculo.get("dataSaida") is None
                        )
                    else:
                        autorizado = False
                    if autorizado:
                        candidatos.add(cid)
            if len(candidatos) == 1:
                data["condominio_id"] = next(iter(candidatos))

        if perfil == COOPERATIVA and data.get("cooperativa_id") is None and data.get("cooperative_id") is None:
            cooperativa = data.get("cooperativa")
            if isinstance(cooperativa, dict):
                cid = cooperativa.get("id")
                if type(cid) is int and cid > 0:
                    data["cooperativa_id"] = cid
        return data

    @field_validator("permissoes", mode="before")
    @classmethod
    def normalize_permissions(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("permissoes deve ser uma lista")
        return [str(item).strip() for item in value if str(item).strip()]


class AuthenticationService:
    """Converte claims de uma API externa em um contexto de domínio seguro."""

    def __init__(self, client: AuthApiClient | None):
        self.client = client

    async def authenticate(self, token: str) -> UserContext:
        if self.client is None:
            raise AuthenticationUnavailable("Integração de autenticação não configurada.")
        try:
            payload = await self.client.validate_token(token)
        except AuthApiUnauthorized as exc:
            raise InvalidToken("Token inválido ou expirado.") from exc
        except (AuthApiUnavailable, AuthApiProtocolError) as exc:
            raise AuthenticationUnavailable("Não foi possível validar a sessão no momento.") from exc

        try:
            identity = AuthIdentity.model_validate(payload)
        except ValidationError as exc:
            raise InvalidUserClaims("A API de autenticação devolveu claims inválidos.") from exc

        profile = normalize_profile(identity.perfil)
        if profile is None:
            raise InvalidUserClaims("A API de autenticação devolveu um perfil não suportado.")
        return UserContext(
            user_id=identity.user_id,
            perfil=profile,
            condominio_id=identity.condominio_id,
            permissoes=identity.permissoes,
            token=token,
            cooperativa_id=identity.cooperativa_id,
        )

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()
