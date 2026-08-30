from __future__ import annotations

from dataclasses import dataclass
import unicodedata

from src.security.roles import (
    COOPERATIVA,
    MORADOR_RESIDENCIAL,
    SINDICO_COMERCIAL,
    SINDICO_RESIDENCIAL,
    USUARIO_COMERCIAL,
    USUARIO_COMUM,
    normalize_profile,
)
from src.shared.context import UserContext


@dataclass(frozen=True, slots=True)
class AccessDecision:
    allowed: bool
    action: str
    message: str = ""


# O perfil é o teto de acesso. Claims explícitos de permissões refinam ações
# sensíveis, mas nunca concedem um agente que o perfil não possui.
ROLE_AGENT_ACCESS: dict[str, set[str]] = {
    USUARIO_COMUM: {"faq", "educacional"},
    SINDICO_RESIDENCIAL: {"faq", "educacional", "analytics", "coletas"},
    SINDICO_COMERCIAL: {"faq", "educacional", "analytics", "coletas"},
    MORADOR_RESIDENCIAL: {"faq", "educacional", "analytics"},
    USUARIO_COMERCIAL: {"faq", "educacional", "analytics", "coletas"},
    COOPERATIVA: {"faq", "coletas"},
}

ROLE_ACTION_ACCESS: dict[str, set[str]] = {
    USUARIO_COMUM: {"faq", "educacional"},
    SINDICO_RESIDENCIAL: {
        "faq",
        "educacional",
        "analytics_individual",
        "analytics_macro",
        "ranking_moradores",
        "ranking_torres",
        "ranking_pessoal",
        "coleta_consultar",
        "coleta_gerenciar",
    },
    SINDICO_COMERCIAL: {
        "faq",
        "educacional",
        "analytics_individual",
        "analytics_macro",
        "coleta_consultar",
        "coleta_gerenciar",
    },
    MORADOR_RESIDENCIAL: {
        "faq",
        "educacional",
        "analytics_individual",
        "ranking_moradores",
        "ranking_torres",
        "ranking_pessoal",
    },
    USUARIO_COMERCIAL: {
        "faq",
        "educacional",
        "analytics_individual",
        "coleta_consultar",
    },
    COOPERATIVA: {"faq", "coleta_consultar", "coleta_gerenciar", "coleta_confirmar", "coleta_agenda"},
}

_SENSITIVE_ACTIONS = {
    "analytics_individual",
    "analytics_macro",
    "ranking_moradores",
    "ranking_torres",
    "ranking_pessoal",
    "coleta_consultar",
    "coleta_gerenciar",
    "coleta_confirmar",
    "coleta_agenda",
}

_CLAIM_ALIASES: dict[str, set[str]] = {
    "analytics_individual": {"analytics", "analytics_individual"},
    "analytics_macro": {"analytics", "analytics_macro"},
    "ranking_moradores": {"ranking", "ranking_moradores"},
    "ranking_torres": {"ranking", "ranking_torres"},
    "ranking_pessoal": {"ranking", "ranking_pessoal"},
    "coleta_consultar": {"coleta", "coletas", "calendar", "calendario", "coleta_consultar"},
    "coleta_gerenciar": {"coleta", "coletas", "calendar", "calendario", "coleta_gerenciar"},
    "coleta_confirmar": {"coleta", "coletas", "calendar", "calendario", "coleta_confirmar"},
    "coleta_agenda": {"coleta", "coletas", "calendar", "calendario", "coleta_agenda"},
}


def _normalized_text(value: str) -> str:
    value = unicodedata.normalize("NFD", value.lower())
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    return " ".join(value.split())


def action_for_route(route: str, message: str) -> str:
    """Determina a ação de maior privilégio implícita pela mensagem."""
    text = _normalized_text(message)
    if route == "analytics":
        # Rankings possuem regras próprias e podem ser visíveis a moradores no
        # contexto do próprio condomínio; consulta multi-condomínio continua macro.
        if "ranking" in text or "posicao" in text or "colocacao" in text:
            if any(marker in text for marker in ("todos os condominios", "outros condominios", "ranking geral")):
                return "analytics_macro"
            if any(marker in text for marker in ("minha posicao", "minha colocacao", "minha classificacao")):
                return "ranking_pessoal"
            if "torre" in text:
                return "ranking_torres"
            return "ranking_moradores"

        macro_markers = (
            "todos os condominios",
            "todos condominios",
            "outros condominios",
            "dados macro",
            "visao macro",
            "geral do condominio",
            "reciclagem em geral",
            "material mais reciclado",
            "mais reciclado no condominio",
            "desempenho do condominio",
            "comparar torres",
            "taxa de aprovacao",
            "quizzes do condominio",
        )
        if any(marker in text for marker in macro_markers):
            return "analytics_macro"
        return "analytics_individual"
    if route == "coletas":
        if any(marker in text for marker in ("confirmar", "vou conseguir passar", "confirmacao de passagem")):
            return "coleta_confirmar"
        if any(marker in text for marker in ("minha agenda", "compromissos futuros", "agenda da cooperativa")):
            return "coleta_agenda"
        if any(marker in text for marker in ("agendar", "remarcar", "alterar", "recorrente", "avulso")):
            return "coleta_gerenciar"
        return "coleta_consultar"
    return route


def authorize_agent(user: UserContext | None, route: str) -> AccessDecision:
    if user is None:
        return AccessDecision(False, route, "Não foi possível validar o contexto de acesso da sua conta.")
    profile = normalize_profile(user.perfil)
    if profile is None:
        return AccessDecision(False, route, "Seu perfil não é reconhecido para acessar este recurso.")
    if route not in ROLE_AGENT_ACCESS[profile]:
        return AccessDecision(False, route, "Seu perfil não possui acesso a este tipo de solicitação.")
    return AccessDecision(True, route)


def authorize_action(user: UserContext | None, action: str) -> AccessDecision:
    if user is None:
        return AccessDecision(False, action, "Não foi possível validar o contexto de acesso da sua conta.")
    profile = normalize_profile(user.perfil)
    if profile is None or action not in ROLE_ACTION_ACCESS.get(profile, set()):
        return AccessDecision(False, action, "Sua conta não possui permissão para esta ação.")

    # Se o provedor de identidade enviou permissões específicas, elas restringem
    # operações sensíveis. A ausência da lista mantém a política do perfil como
    # fonte de decisão para integrações que ainda não expõem granularidade.
    if action in _SENSITIVE_ACTIONS and user.permissoes:
        claims = {_normalized_text(item) for item in user.permissoes}
        aliases = _CLAIM_ALIASES.get(action, {action})
        if not claims.intersection(aliases):
            return AccessDecision(False, action, "Sua conta não recebeu a permissão necessária para esta ação.")
    return AccessDecision(True, action)


def authorize_route(user: UserContext | None, route: str, message: str) -> AccessDecision:
    agent_decision = authorize_agent(user, route)
    if not agent_decision.allowed:
        return agent_decision
    action = action_for_route(route, message)
    action_decision = authorize_action(user, action)
    if action_decision.allowed:
        return action_decision
    if action == "analytics_macro":
        return AccessDecision(
            False,
            action,
            "Seu perfil possui apenas análise individual e não pode consultar rankings ou dados macro de condomínios.",
        )
    return action_decision
