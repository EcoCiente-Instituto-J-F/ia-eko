from __future__ import annotations

import unicodedata

USUARIO_COMUM = "USUARIO_COMUM"
SINDICO_RESIDENCIAL = "SINDICO_RESIDENCIAL"
SINDICO_COMERCIAL = "SINDICO_COMERCIAL"
MORADOR_RESIDENCIAL = "MORADOR_RESIDENCIAL"
USUARIO_COMERCIAL = "USUARIO_COMERCIAL"
COOPERATIVA = "COOPERATIVA"

ALL_PROFILES = {
    USUARIO_COMUM,
    SINDICO_RESIDENCIAL,
    SINDICO_COMERCIAL,
    MORADOR_RESIDENCIAL,
    USUARIO_COMERCIAL,
    COOPERATIVA,
}


def _key(value: str) -> str:
    value = unicodedata.normalize("NFD", value.strip().upper())
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    return "_".join(value.replace("-", " ").split())


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
