from fastapi import APIRouter, Depends

from src.api.dependencies import get_health_service
from src.api.schemas.health import HealthResponse
from src.services.health_service import HealthService

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(service: HealthService = Depends(get_health_service)) -> HealthResponse:
    services = await service.check()
    critical_errors = [value for value in services.values() if value == "error"]
    return HealthResponse(status="degraded" if critical_errors else "ok", services=services)
