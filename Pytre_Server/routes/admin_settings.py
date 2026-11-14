"""
Admin webui routes for the server's general settings (Pytre_Core/settings.py, KeePass-backed,
shared across the whole process) - only the fields the server itself actually reads.
Gated by require_admin_session (see admin_router.py). Saving them is only possible from the server itself
(require_local_connection): from a remote session, an admin could otherwise run their own SQL with the server's
accounts (queries folder), stop the query execution logs to hide it, or leak the NTLM hash of the service account
through a UNC path. The page itself stays visible, read-only.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from Pytre_Core.utils import log as _log
from Pytre_Server.routes.admin_auth import is_loopback, require_local_connection
from Pytre_Server.server_state import ServerState, get_server_state
from Pytre_Server.templating import templates


router = APIRouter(prefix="/settings")


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


@dataclass
class _SettingsContext:
    """What settings.html shows: the setting values, and whether they can be changed from this connection."""

    queries_folder: str | Path
    logs_are_on: bool
    logs_folder: str | Path
    is_local: bool
    msg: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        values = {
            "queries_folder": self.queries_folder,
            "logs_are_on": self.logs_are_on,
            "logs_folder": self.logs_folder,
        }
        return {"values": values, "is_local": self.is_local, "msg": self.msg, "error": self.error}


@router.get("", response_class=HTMLResponse)
def settings_page(
    request: Request,
    state: ServerState = Depends(get_server_state),
    msg: str | None = None,
    error: str | None = None,
) -> HTMLResponse:

    settings = state.app_ctx.settings

    context = _SettingsContext(
        settings.queries_folder, settings.logs_are_on, settings.logs_folder, is_loopback(request), msg, error
    )

    return templates.TemplateResponse(request, "admin/settings.html", context.to_dict())


@router.post("", response_class=HTMLResponse, response_model=None)
def settings_save(
    request: Request,
    state: ServerState = Depends(get_server_state),
    queries_folder: str = Form(""),
    logs_are_on: str | None = Form(None),
    logs_folder: str = Form(""),
    admin_username: str = Depends(require_local_connection),
) -> HTMLResponse | RedirectResponse:

    settings = state.app_ctx.settings
    original = (settings.queries_folder, settings.logs_are_on, settings.logs_folder)

    settings.queries_folder = Path(queries_folder)
    settings.logs_are_on = logs_are_on is not None
    settings.logs_folder = Path(logs_folder)

    try:
        settings.save()
    except (LookupError, ValueError, OSError) as e:
        settings.queries_folder, settings.logs_are_on, settings.logs_folder = original  # restore valid values

        # only a local request gets this far: is_local is True
        context = _SettingsContext(queries_folder, logs_are_on is not None, logs_folder, True, error=str(e))
        return templates.TemplateResponse(request, "admin/settings.html", context.to_dict())

    log(
        f"Settings changed by admin '{admin_username}': queries_folder='{settings.queries_folder}', "
        f"logs_are_on={settings.logs_are_on}, logs_folder='{settings.logs_folder}'",
        logging.WARNING,
    )
    return RedirectResponse(url="/admin/settings?msg=Settings+saved", status_code=status.HTTP_303_SEE_OTHER)
