"""
Admin webui routes for the server's general settings (Pytre_Core/settings.py, KeePass-backed,
shared across the whole process) - only the fields the server itself actually reads.
Gated by require_admin_session (see admin_router.py)
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from Pytre_Core.utils import log as _log
from Pytre_Server.routes.admin_auth import require_admin_session
from Pytre_Server.server_state import ServerState, get_server_state
from Pytre_Server.templating import templates


router = APIRouter(prefix="/settings")


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


@router.get("", response_class=HTMLResponse)
def settings_page(
    request: Request,
    state: ServerState = Depends(get_server_state),
    msg: str | None = None,
    error: str | None = None,
) -> HTMLResponse:

    settings = state.app_ctx.settings

    values = {
        "queries_folder": settings.queries_folder,
        "logs_are_on": settings.logs_are_on,
        "logs_folder": settings.logs_folder,
    }

    context = {"values": values, "msg": msg, "error": error}

    return templates.TemplateResponse(request, "admin/settings.html", context)


@router.post("", response_class=HTMLResponse, response_model=None)
def settings_save(
    request: Request,
    state: ServerState = Depends(get_server_state),
    queries_folder: str = Form(""),
    logs_are_on: str | None = Form(None),
    logs_folder: str = Form(""),
    admin_username: str = Depends(require_admin_session),
) -> HTMLResponse | RedirectResponse:

    settings = state.app_ctx.settings
    original = (settings.queries_folder, settings.logs_are_on, settings.logs_folder)

    settings.queries_folder = Path(queries_folder)
    settings.logs_are_on = logs_are_on is not None
    settings.logs_folder = Path(logs_folder)

    try:
        settings.save()
    except (LookupError, ValueError) as e:
        settings.queries_folder, settings.logs_are_on, settings.logs_folder = original  # restore valid values
        values = {
            "queries_folder": queries_folder,
            "logs_are_on": logs_are_on is not None,
            "logs_folder": logs_folder,
        }
        return templates.TemplateResponse(request, "admin/settings.html", {"values": values, "error": str(e)})

    log(
        f"Settings changed by admin '{admin_username}': queries_folder='{settings.queries_folder}', "
        f"logs_are_on={settings.logs_are_on}, logs_folder='{settings.logs_folder}'",
        logging.INFO,
    )
    return RedirectResponse(url="/admin/settings?msg=Settings+saved", status_code=status.HTTP_303_SEE_OTHER)
