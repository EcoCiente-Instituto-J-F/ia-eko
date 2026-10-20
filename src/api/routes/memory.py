from fastapi import APIRouter, Depends, HTTPException, Response, status

from src.api.dependencies import UserContext, get_session_service, get_user_context
from src.services.session_service import SessionService, StorageUnavailable

router = APIRouter(prefix="/api/v1/memory", tags=["memory"])


@router.get("")
async def get_memory(
    user: UserContext = Depends(get_user_context),
    sessions: SessionService = Depends(get_session_service),
) -> dict:
    """O que o assistente lembra do usuário entre conversas."""
    try:
        memoria = await sessions.get_user_memory(user.user_id)
    except StorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"usuario_id": user.user_id, "memoria": memoria["texto"], "atualizada_em": memoria["updated_at"]}


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    user: UserContext = Depends(get_user_context),
    sessions: SessionService = Depends(get_session_service),
) -> Response:
    """Apaga a memória de longo prazo do usuário (direito de exclusão da LGPD)."""
    try:
        await sessions.delete_user_memory(user.user_id)
    except StorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
