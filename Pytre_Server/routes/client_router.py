"""Routes of the client, gathered in a single router."""

from fastapi import APIRouter

from Pytre_Server.routes.client_auth import router as client_auth_router
from Pytre_Server.routes.client_catalog import router as client_catalog_router
from Pytre_Server.routes.client_jobs import router as client_jobs_router
from Pytre_Server.routes.client_sso import router as client_sso_router


router = APIRouter()
router.include_router(client_auth_router)
router.include_router(client_catalog_router)
router.include_router(client_jobs_router)
router.include_router(client_sso_router)


@router.get("/health")
async def health():
    """Return the health status of the server. Allows clients to do a quick check to confirm server is running."""
    return {"status": "ok"}
