from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse

from Pytre_Core.utils import get_app_path

from Pytre_Server.templating import templates
from Pytre_Server.routes.admin_auth import require_admin_session, router as admin_auth_router
from Pytre_Server.routes.admin_logs import router as admin_logs_router
from Pytre_Server.routes.admin_master_key import router as admin_master_key_router
from Pytre_Server.routes.admin_queries import router as admin_queries_router
from Pytre_Server.routes.admin_servers import router as admin_servers_router
from Pytre_Server.routes.admin_settings import router as admin_settings_router
from Pytre_Server.routes.admin_sso import router as admin_sso_router
from Pytre_Server.routes.admin_users import router as admin_users_router


_RES_DIR = get_app_path() / "res"  # Pytre_Core/res - shared branding assets (app.png/app.ico)

# define a protected router : depends on require_admin_session
protected_router = APIRouter(dependencies=[Depends(require_admin_session)])
protected_router.include_router(admin_logs_router)
protected_router.include_router(admin_master_key_router)
protected_router.include_router(admin_queries_router)
protected_router.include_router(admin_servers_router)
protected_router.include_router(admin_settings_router)
protected_router.include_router(admin_sso_router)
protected_router.include_router(admin_users_router)

# create admin router and attach admin_auth_router and protected router
router = APIRouter(prefix="/admin")
router.include_router(admin_auth_router)
router.include_router(protected_router)


# ------------------------------------------------------------------------------------------
# define additional routes
# ------------------------------------------------------------------------------------------
@router.get("/static/logo.png")
def admin_logo() -> FileResponse:
    return FileResponse(_RES_DIR / "app.png")


@router.get("/favicon.ico")
def admin_favicon() -> FileResponse:
    return FileResponse(_RES_DIR / "app.ico")


@protected_router.get("", response_class=HTMLResponse)
def admin_home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "admin/home.html")
