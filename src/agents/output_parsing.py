"""Parsing tolerante das saídas dos agentes de controle (orquestrador e juízes).

Por que existe
--------------
Os prompts do orquestrador e dos juízes pedem **JSON** na seção "SAÍDA"
(`src/prompts/shared/*.py`), mas o código antigo só reconhecia o formato
`CHAVE=valor` usado pelos few-shots e pelo modo mock. Com um LLM real que
seguisse o contrato em JSON:

- o juiz de saída reprovava 100% das respostas (`"status": "aprovado"` não
  casava com a regex `status\\s*=\\s*aprovado`);
- o juiz de entrada nunca bloqueava (`"status": "bloqueado"` idem);
- o orquestrador caía sempre em `faq` quando respondia `{"route": ...}`;
- `aprovado_com_censura` descartava a versão censurada e exibia o rascunho
  original (vazamento do dado que o juiz tinha acabado de remover).

Este módulo aceita os dois formatos — JSON (inclusive dentro de bloco
Markdown ou com texto em volta) e `CHAVE=valor` — e devolve decisões
estruturadas, com categoria de motivo de cardinalidade fechada para métricas.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

VALID_ROUTES = ("coletas", "educacional", "analytics", "grafo", "faq")
_ROUTE_ALIASES = {"educador": "educacional"}

# Categorias fechadas — viram label de métrica Prometheus, então NUNCA podem
# vir de texto livre do LLM (cardinalidade explodiria).
OUTPUT_JUDGE_CATEGORIES = (
    "dado_identificavel_terceiro",
    "recomendacao_administrativa_indevida",
    "afirmacao_nao_fundamentada",
    "resposta_vazia",
    "saida_invalida",
    "outro",
)
INPUT_JUDGE_CATEGORIES = (
    "prompt_injection",
    "dados_privados_terceiro",
    "acao_administrativa_indevida",
    "conteudo_ofensivo_discriminatorio",
    "suspeita_falsificacao_perfil",
    "saida_invalida",
    "outro",
)


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Retorna o primeiro objeto JSON válido encontrado no texto, se houver."""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text, flags=re.I)
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", cleaned):
        try:
            value, _ = decoder.raw_decode(cleaned[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _kv(text: str, key: str) -> str | None:
    match = re.search(rf"\b{key}\s*=\s*([^\n]+)", text, re.I)
    return match.group(1).strip() if match else None


def _status_token(raw: str | None) -> str:
    return (raw or "").strip().strip("\"'").lower()


# --------------------------------------------------------------------------- #
# Orquestrador
# --------------------------------------------------------------------------- #


def parse_route(text: str) -> str:
    """`faq` é o fallback seguro: é o especialista de menor privilégio."""
    data = extract_json_object(text)
    candidate = None
    if data is not None:
        candidate = data.get("route") or data.get("rota")
    if not candidate:
        candidate = _kv(text, "ROUTE") or _kv(text, "ROTA")
    if not candidate:
        return "faq"
    token = re.split(r"[\s|,]", str(candidate).strip().strip("\"'").lower())[0]
    token = _ROUTE_ALIASES.get(token, token)
    return token if token in VALID_ROUTES else "faq"


# --------------------------------------------------------------------------- #
# Juiz de entrada
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class InputJudgeDecision:
    blocked: bool
    category: str = "outro"
    reason: str = ""


def parse_input_judge(text: str) -> InputJudgeDecision:
    """Falha aberta (aprova) quando a saída é ilegível.

    A camada determinística (`src/security/guardrails.py`) roda antes e
    continua bloqueando os padrões conhecidos; bloquear toda mensagem por
    causa de uma saída malformada do LLM derrubaria o chatbot inteiro.
    A ocorrência é contada como `saida_invalida` para aparecer no dashboard.
    """
    data = extract_json_object(text)
    if data is not None:
        status = _status_token(str(data.get("status", "")))
        if status == "bloqueado":
            category = str(data.get("categoria", "")).strip().lower()
            return InputJudgeDecision(
                blocked=True,
                category=category if category in INPUT_JUDGE_CATEGORIES else "outro",
                reason=str(data.get("motivo_interno", ""))[:300],
            )
        if status == "aprovado":
            return InputJudgeDecision(blocked=False)
    status = _status_token(_kv(text, "STATUS"))
    if status.startswith("bloqueado"):
        category = (_kv(text, "CATEGORIA") or "outro").strip().lower()
        return InputJudgeDecision(
            blocked=True,
            category=category if category in INPUT_JUDGE_CATEGORIES else "outro",
        )
    if status.startswith("aprovado"):
        return InputJudgeDecision(blocked=False)
    return InputJudgeDecision(blocked=False, category="saida_invalida")


# --------------------------------------------------------------------------- #
# Juiz de saída
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class OutputJudgeDecision:
    aprovado: bool
    motivo: str
    categoria: str
    necessita_correcao: bool
    resposta_censurada: str | None = None
    criterios: list[str] = field(default_factory=list)

    def as_state(self) -> dict[str, Any]:
        return {
            "aprovado": self.aprovado,
            "motivo": self.motivo,
            "categoria": self.categoria,
            "necessita_correcao": self.necessita_correcao,
            "resposta_censurada": self.resposta_censurada,
        }


def _criterios(data: dict[str, Any]) -> list[str]:
    result = []
    for item in data.get("campos_censurados") or []:
        if isinstance(item, dict):
            criterio = str(item.get("criterio", "")).strip().lower()
            if criterio:
                result.append(criterio)
    return result


def _category_from(criterios: list[str], motivo: str) -> str:
    for criterio in criterios:
        if criterio in OUTPUT_JUDGE_CATEGORIES:
            return criterio
    lowered = motivo.lower()
    if any(k in lowered for k in ("terceiro", "identific", "privacidade", "dado pessoal")):
        return "dado_identificavel_terceiro"
    if any(k in lowered for k in ("administrativ", "penaliz", "disciplinar")):
        return "recomendacao_administrativa_indevida"
    if any(k in lowered for k in ("fundament", "evidência", "evidencia", "alucin", "não consta", "nao consta")):
        return "afirmacao_nao_fundamentada"
    return "outro"


def parse_output_judge(text: str) -> OutputJudgeDecision:
    data = extract_json_object(text)
    if data is not None:
        status = _status_token(str(data.get("status", "")))
        criterios = _criterios(data)
        motivo = str(data.get("motivo_interno") or data.get("motivo") or "").strip()
        if status == "aprovado":
            return OutputJudgeDecision(True, "aprovado", "aprovado", False, criterios=criterios)
        if status == "aprovado_com_censura":
            final = str(data.get("resposta_final") or "").strip()
            if final:
                return OutputJudgeDecision(
                    True,
                    motivo or "aprovado_com_censura",
                    _category_from(criterios, motivo),
                    False,
                    resposta_censurada=final,
                    criterios=criterios,
                )
            # Censura anunciada sem texto censurado: não dá para exibir o
            # rascunho original com segurança.
            return OutputJudgeDecision(False, "censura_sem_resposta_final", "saida_invalida", True, criterios=criterios)
        if status == "bloqueado":
            return OutputJudgeDecision(
                False,
                motivo or "bloqueado pelo juiz de saída",
                _category_from(criterios, motivo),
                True,
                criterios=criterios,
            )

    status = _status_token(_kv(text, "STATUS"))
    motivo = (_kv(text, "MOTIVO") or "").strip()
    # Comparação exata: "aprovado_com_censura" NÃO pode passar como "aprovado".
    if status == "aprovado":
        return OutputJudgeDecision(True, "aprovado", "aprovado", False)
    if status in {"reprovado", "bloqueado"}:
        return OutputJudgeDecision(False, motivo or "Resposta não aprovada pelo juiz.", _category_from([], motivo), True)
    return OutputJudgeDecision(False, "Saída do juiz ilegível.", "saida_invalida", True)
