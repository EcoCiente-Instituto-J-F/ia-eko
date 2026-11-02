import pytest
from pydantic import ValidationError

from src.security.authentication import AuthIdentity
from src.security.roles import MORADOR_RESIDENCIAL, normalize_profile


@pytest.mark.parametrize("campo", ["perfil", "profile", "tipoUsuario", "tipo_usuario"])
def test_identidade_aceita_campos_de_perfil(campo):
    identidade = AuthIdentity.model_validate({"id": 42, campo: "MORADOR_RESIDENCIAL"})
    assert identidade.user_id == 42
    assert normalize_profile(identidade.perfil) == MORADOR_RESIDENCIAL


def test_identidade_nao_aceita_perfil_ausente():
    with pytest.raises(ValidationError):
        AuthIdentity.model_validate({"id": 42})


def test_identidade_nao_aceita_usuario_sem_id():
    with pytest.raises(ValidationError):
        AuthIdentity.model_validate({"tipoUsuario": "MORADOR_RESIDENCIAL"})
