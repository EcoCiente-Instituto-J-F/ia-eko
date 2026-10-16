from __future__ import annotations

from dataclasses import dataclass

from src.core.config import Settings
from src.observability.metrics import CHAT_COST_BRL, CHATS_TOTAL

SCENARIOS = (100, 1000)


def chat_cost_brl(settings: Settings, input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens * settings.llm_price_input_per_1m_brl
        + output_tokens * settings.llm_price_output_per_1m_brl
    ) / 1_000_000


def _div(a: float, b: float, digits: int) -> float | None:
    return round(a / b, digits) if b else None


@dataclass
class CostLedger:
    """Acumulador em processo das respostas do /chat.

    ponytail: zera no restart e é por réplica; a fonte entre réplicas são os
    contadores Prometheus (ecociente_chat_cost_brl_total / ecociente_chats_total).
    """

    requests: int = 0
    resolved: int = 0
    cost_brl: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, cost_brl: float, input_tokens: int, output_tokens: int, resolved: bool) -> None:
        self.requests += 1
        self.resolved += int(resolved)
        self.cost_brl += cost_brl
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens

    def snapshot(self) -> dict:
        return {
            "solicitacoes": self.requests,
            "resolvidas": self.resolved,
            "taxa_resolucao": _div(self.resolved, self.requests, 4),
            "custo_total_brl": round(self.cost_brl, 6),
            "tokens_entrada": self.input_tokens,
            "tokens_saida": self.output_tokens,
        }

    def project(self, settings: Settings, users: int) -> dict:
        infra = settings.cost_infra_weekly_brl_100 if users == 100 else settings.cost_infra_weekly_brl_1000
        requests = users * settings.cost_requests_per_user_week
        if not self.requests:
            # Sem amostras não há média medida: não inventar custo/resolução/ROI.
            return {
                "usuarios_semanais": users,
                "solicitacoes_semana": requests,
                "custo_api_brl": None,
                "custo_infra_brl": infra,
                "custo_total_brl": None,
                "custo_por_solicitacao_brl": None,
                "resolucoes_semana": None,
                "custo_por_resolucao_brl": None,
                "beneficio_brl": None,
                "roi": None,
            }
        avg_cost = self.cost_brl / self.requests if self.requests else 0.0
        rate = self.resolved / self.requests if self.requests else 0.0
        api = requests * avg_cost
        total = api + infra
        resolutions = requests * rate
        benefit = resolutions * settings.cost_benefit_per_resolution_brl
        return {
            "usuarios_semanais": users,
            "solicitacoes_semana": requests,
            "custo_api_brl": round(api, 4),
            "custo_infra_brl": infra,
            "custo_total_brl": round(total, 4),
            "custo_por_solicitacao_brl": _div(total, requests, 6),
            "resolucoes_semana": round(resolutions, 2),
            "custo_por_resolucao_brl": _div(total, resolutions, 6),
            "beneficio_brl": round(benefit, 4),
            "roi": _div(benefit - total, total, 4),
        }


LEDGER = CostLedger()


def record_chat(settings: Settings, input_tokens: int, output_tokens: int, resolved: bool) -> None:
    cost = chat_cost_brl(settings, input_tokens, output_tokens)
    LEDGER.add(cost, input_tokens, output_tokens, resolved)
    CHAT_COST_BRL.inc(cost)
    CHATS_TOTAL.labels(resolved="true" if resolved else "false").inc()
