from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True)
class GuardrailDecision:
    allowed: bool
    reason: str = "aprovado"
    safe_message: str | None = None


_INJECTION_PATTERNS = [
    r"ignore (todas|as|qualquer|suas|previous|all).*instru",
    r"revele .*?(prompt|system prompt|instru[cç][oõ]es internas)",
    r"mostre .*?(chave|api key|token|senha|credencial|\.env)",
    r"developer message|system message|chain of thought|racioc[ií]nio interno",
    r"fa[cç]a de conta que .*?(regra|pol[ií]tica|guardrail).*(n[aã]o existe|ignora)",
]

_PRIVACY_PATTERNS = [
    r"(cpf|telefone|e-?mail|endere[cç]o|dados pessoais).{0,35}(outro|terceiro|vizinho|morador)",
    r"dados privados.{0,30}(outro|terceiro|morador)",
]

_FRAUD_PATTERNS = [
    r"(alterar|aumentar|forjar|fraudar|manipular).{0,30}(pontos|pontua[cç][aã]o|ranking)",
    r"burlar.{0,30}(ranking|pontua[cç][aã]o|valida[cç][aã]o)",
]

_SECRET_PATTERNS = [
    r"\b(sk-[A-Za-z0-9_-]{12,})\b",
    r"(?i)(api[_ -]?key|token|password|senha)\s*[:=]\s*[^\s,;]{8,}",
]

_CPF_RE = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)


def validate_input(message: str) -> GuardrailDecision:
    normalized = " ".join(message.lower().split())
    for pattern in _INJECTION_PATTERNS:
        if re.search(pattern, normalized, re.I):
            return GuardrailDecision(False, "prompt_injection", "Não posso seguir instruções que tentem alterar ou expor regras internas do sistema.")
    for pattern in _PRIVACY_PATTERNS:
        if re.search(pattern, normalized, re.I):
            return GuardrailDecision(False, "privacy_third_party", "Não posso fornecer dados pessoais ou privados de outros usuários.")
    for pattern in _FRAUD_PATTERNS:
        if re.search(pattern, normalized, re.I):
            return GuardrailDecision(False, "ranking_fraud", "Não posso ajudar a manipular pontuação, validações ou rankings do EcoCiente.")
    return GuardrailDecision(True)


def sanitize_output(text: str) -> str:
    safe = text
    safe = _CPF_RE.sub("[CPF REDIGIDO]", safe)
    safe = _EMAIL_RE.sub("[E-MAIL REDIGIDO]", safe)
    for pattern in _SECRET_PATTERNS:
        safe = re.sub(pattern, "[SEGREDO REDIGIDO]", safe)
    return safe
