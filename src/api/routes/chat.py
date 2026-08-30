from fastapi import APIRouter, Depends, HTTPException, status

from src.api.dependencies import UserContext, get_chat_service, get_user_context, require_same_user
from src.api.schemas.chat import ChatRequest, ChatResponse
from src.observability.middleware import current_request_id
from src.services.chat_service import ChatService
from src.services.session_service import SessionForbidden, SessionNotFound, StorageUnavailable

router = APIRouter(prefix="/api/v1", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    user: UserContext = Depends(get_user_context),
    service: ChatService = Depends(get_chat_service),
) -> ChatResponse:
    require_same_user(payload.usuario_id, user)
    try:
        return await service.chat(
            payload,
            request_id=current_request_id(),
            user_context=user,
        )
    except SessionForbidden as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except SessionNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except StorageUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
