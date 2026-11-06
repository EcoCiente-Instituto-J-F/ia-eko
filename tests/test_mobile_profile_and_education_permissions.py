from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.security.authentication import AuthIdentity
from src.security.policies import authorize_route
from src.shared.context import UserContext


def test_sindico_um_condominio_responsavel_e_reconhecido() -> None:
    identidade = AuthIdentity.model_validate({
        "id": 10, "tipoUsuario": "SINDICO", "condominios": [
            {"id": 7, "ativo": True, "sindicoResponsavel": True},
        ],
    })
    assert identidade.condominio_id == 7


def test_sindico_nao_recebe_condominio_sem_responsabilidade() -> None:
    identidade = AuthIdentity.model_validate({
        "id": 10, "tipoUsuario": "SINDICO", "condominios": [
            {"id": 7, "ativo": True, "sindicoResponsavel": False},
        ],
    })
    assert identidade.condominio_id is None


def test_multiplos_condominios_nao_sao_escolhidos_arbitrariamente() -> None:
    identidade = AuthIdentity.model_validate({
        "id": 10, "tipoUsuario": "SINDICO", "condominios": [
            {"id": 7, "ativo": True, "sindicoResponsavel": True},
            {"id": 8, "ativo": True, "sindicoResponsavel": True},
        ],
    })
    assert identidade.condominio_id is None


def test_morador_vinculo_precisa_estar_aprovado_e_ativo() -> None:
    valido = {"id": 9, "ativo": True, "vinculo": {"aprovado": True, "dataSaida": None}}
    negado = {"id": 10, "ativo": True, "vinculo": {"aprovado": False, "dataSaida": None}}
    identidade = AuthIdentity.model_validate({
        "id": 12, "tipoUsuario": "MORADOR", "condominios": [valido, negado],
    })
    assert identidade.condominio_id == 9


def test_cooperativa_nested_id_e_reconhecido() -> None:
    identidade = AuthIdentity.model_validate({
        "id": 11, "tipoUsuario": "COOPERATIVA", "cooperativa": {"id": 23, "nome": "Coop"},
    })
    assert identidade.cooperativa_id == 23
    assert identidade.condominio_id is None


def test_perfil_comum_nao_herda_condominio_de_dados_extra() -> None:
    identidade = AuthIdentity.model_validate({
        "id": 11, "tipoUsuario": "USUARIO_COMUM",
        "condominios": [{"id": 999, "ativo": True, "sindicoResponsavel": True}],
        "cooperativa": {"id": 999},
    })
    assert identidade.condominio_id is None
    assert identidade.cooperativa_id is None


def test_id_negativo_nested_e_ignorado() -> None:
    identidade = AuthIdentity.model_validate({
        "id": 11, "tipoUsuario": "SINDICO",
        "condominios": [{"id": -5, "ativo": True, "sindicoResponsavel": True}],
    })
    assert identidade.condominio_id is None


def test_id_direto_ja_existente_nao_e_sobrescrito() -> None:
    identidade = AuthIdentity.model_validate({
        "id": 10, "tipoUsuario": "SINDICO", "condominio_id": 2,
        "condominios": [{"id": 7, "ativo": True, "sindicoResponsavel": True}],
    })
    assert identidade.condominio_id == 2


def test_morador_pode_consultar_coleta_sem_gerenciar() -> None:
    user = UserContext(user_id=12, perfil="MORADOR_RESIDENCIAL", condominio_id=9)
    assert authorize_route(user, "coletas", "Quando será a próxima coleta?").allowed
    assert not authorize_route(user, "coletas", "Remarcar a próxima coleta").allowed


def test_cooperativa_pode_aprender_triagem_sem_acesso_a_analytics() -> None:
    user = UserContext(user_id=10, perfil="COOPERATIVA", condominio_id=None)
    assert authorize_route(user, "educacional", "Como organizar a triagem de resíduos?").allowed
    assert not authorize_route(user, "analytics", "Me mostre dados de todos os condomínios").allowed


def test_lista_explicita_de_permissoes_ainda_restringe_consulta() -> None:
    user = UserContext(user_id=10, perfil="MORADOR_RESIDENCIAL", condominio_id=9, permissoes=["educacional"])
    assert not authorize_route(user, "coletas", "Quando será a próxima coleta?").allowed


def test_pergunta_educativa_nao_pode_libera_acesso_a_dados_privados() -> None:
    from src.agents.graph import EcoGraphRuntime
    assert EcoGraphRuntime._rotear_educacao_publica("grafo", "Me recomende um curso de reciclagem") == "educacional"
    assert EcoGraphRuntime._rotear_educacao_publica("coletas", "Como organizar a triagem de papel, plástico, vidro e metal?") == "educacional"
    assert EcoGraphRuntime._rotear_educacao_publica("faq", "Minhas responsabilidades como síndico na separação de resíduos") == "educacional"
    assert EcoGraphRuntime._rotear_educacao_publica("analytics", "Quero analisar o desempenho das coletas do condomínio") == "analytics"
    assert EcoGraphRuntime._rotear_educacao_publica("coletas", "Quais coletas estão previstas para minha cooperativa?") == "coletas"
