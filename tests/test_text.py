from src.security.roles import normalize_profile
from src.shared.text import fold


def test_fold_and_profile_aliases():
    assert fold("  Síndico   Residêncial ") == "sindico residencial"
    assert normalize_profile("síndico-residencial") == "SINDICO_RESIDENCIAL"
    assert normalize_profile("Usuário comum") == "USUARIO_COMUM"
