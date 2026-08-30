from fastapi import APIRouter, Depends, HTTPException, Response, status

from src.api.dependencies import UserContext, get_session_service, get_user_context, require_same_user
from src.api.schemas.session import SessionCreateRequest, SessionResponse
from src.services.session_service import SessionForbidden, SessionNotFound, SessionService

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


def _response(doc: dict) -> SessionResponse:
    return SessionResponse(
        session_id=doc["session_id"],
        usuario_id=int(doc["usuario_id"]),
        status=doc.get("status", "active"),
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
        ultima_rota=doc.get("ultima_rota"),
        resumo_parcial=doc.get("memory_summary", doc.get("resumo_parcial", "")),
    )


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: SessionCreateRequest,
    user: UserContext = Depends(get_user_context),
    sessions: SessionService = Depends(get_session_service),
) -> SessionResponse:
    require_same_user(payload.usuario_id, user)
    return _response(await sessions.create_session(user.user_id))


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    user: UserContext = Depends(get_user_context),
    sessions: SessionService = Depends(get_session_service),
) -> SessionResponse:
    try:
        return _response(await sessions.get_session(session_id, user.user_id))
    except SessionForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    user: UserContext = Depends(get_user_context),
    sessions: SessionService = Depends(get_session_service),
) -> Response:
    try:
        await sessions.close_session(session_id, user.user_id)
    except SessionForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
