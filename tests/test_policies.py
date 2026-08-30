from src.security.policies import action_for_route, authorize_route
from src.shared.context import UserContext


def _resident(*, permissions: list[str] | None = None) -> UserContext:
    return UserContext(
        user_id=10,
        perfil="MORADOR_RESIDENCIAL",
        condominio_id=7,
        permissoes=permissions or [],
    )


def test_resident_material_macro_question_is_blocked() -> None:
    decision = authorize_route(_resident(), "analytics", "Qual material mais reciclado no condomínio?")
    assert decision.allowed is False
    assert decision.action == "analytics_macro"


def test_resident_can_access_condominium_resident_ranking_by_profile_policy() -> None:
    decision = authorize_route(_resident(), "analytics", "Mostre o ranking de moradores")
    assert decision.allowed is True
    assert decision.action == "ranking_moradores"


def test_resident_cannot_access_cross_condominium_ranking() -> None:
    decision = authorize_route(_resident(), "analytics", "Mostre o ranking de todos os condomínios")
    assert decision.allowed is False
    assert decision.action == "analytics_macro"


def test_explicit_permissions_restrict_sensitive_action() -> None:
    decision = authorize_route(_resident(permissions=["analytics"]), "analytics", "Mostre o ranking de moradores")
    assert decision.allowed is False
    assert decision.action == "ranking_moradores"


def test_individual_analytics_remains_available_to_resident() -> None:
    assert action_for_route("analytics", "Como está meu desempenho?") == "analytics_individual"
    decision = authorize_route(_resident(), "analytics", "Como está meu desempenho?")
    assert decision.allowed is True
