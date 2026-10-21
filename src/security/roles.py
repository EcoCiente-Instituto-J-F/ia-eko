from __future__ import annotations

from src.shared.text import fold

USUARIO_COMUM = "USUARIO_COMUM"
SINDICO_RESIDENCIAL = "SINDICO_RESIDENCIAL"
SINDICO_COMERCIAL = "SINDICO_COMERCIAL"
MORADOR_RESIDENCIAL = "MORADOR_RESIDENCIAL"
USUARIO_COMERCIAL = "USUARIO_COMERCIAL"
COOPERATIVA = "COOPERATIVA"


def _key(value: str) -> str:
    return "_".join(fold(value.replace("-", " ")).upper().split())


PROFILE_ALIASES = {
    "COMUM": USUARIO_COMUM,
    "USUARIO_COMUM": USUARIO_COMUM,
    "SINDICO": SINDICO_RESIDENCIAL,
    "SINDICO_RESIDENCIAL": SINDICO_RESIDENCIAL,
    "SINDICO_COMERCIAL": SINDICO_COMERCIAL,
    "MORADOR": MORADOR_RESIDENCIAL,
    "MORADOR_RESIDENCIAL": MORADOR_RESIDENCIAL,
    "USUARIO_COMERCIAL": USUARIO_COMERCIAL,
    "COOPERATIVA": COOPERATIVA,
}


def normalize_profile(value: str | None) -> str | None:
    """Normaliza apenas os perfis oficialmente suportados pela política."""
    if not value:
        return None
    return PROFILE_ALIASES.get(_key(value))
