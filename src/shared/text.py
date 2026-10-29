import unicodedata


def fold(value: str) -> str:
    """Minúsculas, sem acentos e com espaços normalizados."""
    value = unicodedata.normalize("NFD", value.lower())
    return " ".join("".join(ch for ch in value if unicodedata.category(ch) != "Mn").split())
