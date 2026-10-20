from fastapi import APIRouter, Request
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from src.observability.cost import LEDGER, SCENARIOS

router = APIRouter(tags=["observability"])


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get("/api/v1/costs")
async def costs(request: Request) -> dict:
    """Custo, custo por resolução e ROI projetados para 100 e 1000 usuários/semana,
    a partir das médias medidas neste processo e das premissas do ambiente."""
    s = request.app.state.settings
    return {
        "amostras": LEDGER.snapshot(),
        "premissas": {
            "solicitacoes_por_usuario_semana": s.cost_requests_per_user_week,
            "preco_entrada_por_1m_brl": s.llm_price_input_per_1m_brl,
            "preco_saida_por_1m_brl": s.llm_price_output_per_1m_brl,
            "beneficio_por_resolucao_brl": s.cost_benefit_per_resolution_brl,
            "infra_semanal_100_brl": s.cost_infra_weekly_brl_100,
            "infra_semanal_1000_brl": s.cost_infra_weekly_brl_1000,
        },
        "cenarios": [LEDGER.project(s, n) for n in SCENARIOS],
    }
