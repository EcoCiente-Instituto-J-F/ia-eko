import json
from contextlib import contextmanager
from typing import Any, AsyncIterator, Iterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from src.api.dependencies import UserContext, get_chat_service, get_user_context, require_same_user
from src.api.schemas.chat import ChatRequest, ChatResponse, QuotaInfo
from src.observability.middleware import current_request_id
from src.services.chat_service import ChatService
from src.services.quota_service import QuotaExceeded
from src.services.session_service import SessionForbidden, SessionNotFound, StorageUnavailable

router = APIRouter(prefix="/api/v1", tags=["chat"])


@contextmanager
def _http_errors() -> Iterator[None]:
    try:
        yield
    except QuotaExceeded as exc:
        headers: dict[str, str] = {"X-Quota-Kind": exc.kind}
        if exc.limit is not None:
            headers["X-RateLimit-Limit"] = str(exc.limit)
            headers["X-RateLimit-Remaining"] = "0"
        if exc.retry_after is not None:
            headers["Retry-After"] = str(exc.retry_after)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"codigo": f"limite_{exc.kind}", "mensagem": exc.message},
            headers=headers,
        ) from exc
    except SessionForbidden as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except SessionNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except StorageUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    user: UserContext = Depends(get_user_context),
    service: ChatService = Depends(get_chat_service),
) -> ChatResponse:
    require_same_user(payload.usuario_id, user)
    with _http_errors():
        return await service.chat(
            payload,
            request_id=current_request_id(),
            user_context=user,
        )


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.post(
    "/chat/stream",
    responses={200: {"content": {"text/event-stream": {}}, "description": "Eventos SSE: session, progress, answer, done, error"}},
)
async def chat_stream(
    payload: ChatRequest,
    user: UserContext = Depends(get_user_context),
    service: ChatService = Depends(get_chat_service),
) -> StreamingResponse:
    """Mesmo pipeline do /chat, emitindo o progresso de cada etapa via SSE.

    Eventos: `session` → `progress`* → `answer` → `done` (payload igual ao
    /chat). Limites e erros de sessão viram HTTP 4xx antes do stream abrir.
    """
    require_same_user(payload.usuario_id, user)
    request_id = current_request_id()
    with _http_errors():
        session, quota_status = await service.admit(payload, user)

    async def events() -> AsyncIterator[str]:
        async for event, data in service.stream(
            payload,
            request_id=request_id,
            user_context=user,
            session=session,
            quota_status=quota_status,
        ):
            yield _sse(event, data)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/chat/quota", response_model=QuotaInfo)
async def chat_quota(
    request: Request,
    user: UserContext = Depends(get_user_context),
) -> QuotaInfo:
    """Quanto o usuário autenticado ainda pode usar hoje (para o front exibir)."""
    quotas = request.app.state.quotas
    return QuotaInfo.from_status(await quotas.status(user))
