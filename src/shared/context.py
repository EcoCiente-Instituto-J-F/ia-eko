from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class UserContext:
    """Identidade autenticada recebida na borda da aplicação.

    A instância original pode carregar o token apenas enquanto a request está
    na camada de infraestrutura. Antes do LangGraph, use :meth:`without_token`;
    prompts usam somente :meth:`for_agent`. O token nunca deve entrar em
    checkpoints, memória, logs ou respostas HTTP.
    """

    user_id: int
    perfil: str
    condominio_id: int | None
    permissoes: list[str] = field(default_factory=list)
    token: str = field(default="", repr=False)
    cooperativa_id: int | None = None

    def for_agent(self) -> dict[str, Any]:
        """Retorna somente claims seguros para uso por agentes e prompts."""
        return {
            "user_id": self.user_id,
            "perfil": self.perfil,
            "condominio_id": self.condominio_id,
            "permissoes": list(self.permissoes),
            "cooperativa_id": self.cooperativa_id,
        }

    def without_token(self) -> "UserContext":
        """Cópia segura para estado/checkpoints e prompts internos."""
        return UserContext(
            user_id=self.user_id,
            perfil=self.perfil,
            condominio_id=self.condominio_id,
            permissoes=list(self.permissoes),
            cooperativa_id=self.cooperativa_id,
        )

    def has_permission(self, permission: str) -> bool:
        normalized = permission.strip().lower()
        return normalized in {item.strip().lower() for item in self.permissoes}
