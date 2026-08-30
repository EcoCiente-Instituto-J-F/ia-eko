from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.dependencies import UserContext, get_ranking_service, get_user_context
from src.api.schemas.ranking import MyRankingResponse, RankingResponse
from src.security.policies import authorize_action
from src.services.ranking_service import RankingService, RankingUnavailable

router = APIRouter(prefix="/api/v1/rankings", tags=["rankings"])


def _condominio(user: UserContext, query_value: int | None) -> int:
    value = query_value or user.condominio_id
    if value is None:
        raise HTTPException(status_code=400, detail="Informe condominio_id ou o header X-Condominio-Id.")
    if user.condominio_id is not None and value != user.condominio_id:
        raise HTTPException(status_code=403, detail="Acesso a outro condomínio não permitido.")
    return value


def _require_action(user: UserContext, action: str) -> None:
    decision = authorize_action(user, action)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.message)


@router.get("/moradores", response_model=RankingResponse)
async def moradores(
    ciclo_id: int = Query(..., gt=0),
    condominio_id: int | None = Query(None, gt=0),
    limit: int = Query(10, ge=1, le=100),
    user: UserContext = Depends(get_user_context),
    rankings: RankingService = Depends(get_ranking_service),
) -> RankingResponse:
    _require_action(user, "ranking_moradores")
    try:
        return await rankings.moradores(_condominio(user, condominio_id), ciclo_id, limit)
    except RankingUnavailable as exc:
        raise HTTPException(status_code=503, detail="Ranking temporariamente indisponível.") from exc


@router.get("/torres", response_model=RankingResponse)
async def torres(
    ciclo_id: int = Query(..., gt=0),
    condominio_id: int | None = Query(None, gt=0),
    limit: int = Query(10, ge=1, le=100),
    user: UserContext = Depends(get_user_context),
    rankings: RankingService = Depends(get_ranking_service),
) -> RankingResponse:
    _require_action(user, "ranking_torres")
    try:
        return await rankings.torres(_condominio(user, condominio_id), ciclo_id, limit)
    except RankingUnavailable as exc:
        raise HTTPException(status_code=503, detail="Ranking temporariamente indisponível.") from exc


@router.get("/me", response_model=MyRankingResponse)
async def me(
    ciclo_id: int = Query(..., gt=0),
    condominio_id: int | None = Query(None, gt=0),
    user: UserContext = Depends(get_user_context),
    rankings: RankingService = Depends(get_ranking_service),
) -> MyRankingResponse:
    _require_action(user, "ranking_pessoal")
    try:
        return await rankings.me(user.user_id, _condominio(user, condominio_id), ciclo_id)
    except RankingUnavailable as exc:
        raise HTTPException(status_code=503, detail="Ranking temporariamente indisponível.") from exc
