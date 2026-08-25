from fastapi import APIRouter, Request

router = APIRouter(tags=["system"])


@router.get("/")
async def root(request: Request) -> dict[str, str]:
    settings = request.app.state.settings
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "online",
        "docs": "/docs",
    }
