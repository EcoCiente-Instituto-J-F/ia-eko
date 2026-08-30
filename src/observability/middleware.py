from __future__ import annotations

import contextvars
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.observability.metrics import REQUEST_ERRORS, REQUEST_LATENCY, REQUESTS_TOTAL

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
logger = logging.getLogger("ecociente.http")


def current_request_id() -> str:
    return request_id_var.get()


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        route_label = request.url.path
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        except Exception:
            REQUEST_ERRORS.labels(method=request.method, route=route_label).inc()
            logger.exception(
                "http_request_failed",
                extra={"request_id": request_id, "route": route_label, "method": request.method},
            )
            raise
        finally:
            duration = time.perf_counter() - started
            REQUESTS_TOTAL.labels(method=request.method, route=route_label, status=str(status)).inc()
            REQUEST_LATENCY.labels(method=request.method, route=route_label).observe(duration)
            if status >= 500:
                REQUEST_ERRORS.labels(method=request.method, route=route_label).inc()
            logger.info(
                "http_request",
                extra={
                    "request_id": request_id,
                    "route": route_label,
                    "method": request.method,
                    "status": status,
                    "duration_ms": round(duration * 1000, 2),
                },
            )
            request_id_var.reset(token)
