"""
Admin webui routes for the server's database master password.
Gated by require_admin_session (see admin_router.py)

- Set/Delete change only the value stored in the OS keyring - what the server will try to use
  on its NEXT restart. If the new value doesn't match what actually protects the database file,
  the server will fail to start until this is corrected.

- Change re-encrypts Pytre.db with a new password immediately and updates the keyring to match -
  so the running process keeps working and the next restart still succeeds.

Bootstrap/disaster-recovery tooling, not a routine "change password" action.
"""

import logging

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from Pytre_Core.utils import log as _log

from Pytre_Server.master_key import delete_master_key, get_master_key, set_master_key
from Pytre_Server.routes.admin_auth import require_admin_session
from Pytre_Server.server_state import ServerState, get_server_state
from Pytre_Server.templating import templates


router = APIRouter(prefix="/master-key")


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


def _context(msg: str | None = None, error: str | None = None) -> dict:
    return {
        "key_exists": get_master_key() is not None,
        "msg": msg,
        "error": error,
    }


@router.get("", response_class=HTMLResponse)
def master_key_page(request: Request, msg: str | None = None, error: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(request, "admin/master_key.html", _context(msg=msg, error=error))


@router.get("/set-form", response_class=HTMLResponse)
def master_key_set_form(request: Request, error: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(request, "admin/master_key_set_form.html", {"error": error})


@router.post("/reveal")
def master_key_reveal(admin_username: str = Depends(require_admin_session)) -> dict[str, str | None]:
    log(f"Master key revealed by admin '{admin_username}'", logging.WARNING)
    return {"value": get_master_key()}


@router.post("/delete")
def master_key_delete(admin_username: str = Depends(require_admin_session)) -> RedirectResponse:
    delete_master_key()
    log(f"Master key deleted from keyring by admin '{admin_username}'", logging.WARNING)
    return RedirectResponse(url="/admin/master-key?msg=Master+key+deleted", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/set", response_class=HTMLResponse, response_model=None)
def master_key_set(
    request: Request,
    password: str = Form(""),
    confirm_password: str = Form(""),
    admin_username: str = Depends(require_admin_session),
) -> HTMLResponse | RedirectResponse:

    error: str | None = None
    if not password:
        error = "Password is required"
    elif password != confirm_password:
        error = "Passwords do not match"

    if error:
        return templates.TemplateResponse(request, "admin/master_key_set_form.html", _context(error=error))

    set_master_key(password)
    log(f"Master key set/changed in keyring by admin '{admin_username}'", logging.WARNING)
    return RedirectResponse(url="/admin/master-key?msg=Master+key+saved", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/change-db-password", response_class=HTMLResponse, response_model=None)
def master_key_change_db_password(
    request: Request,
    state: ServerState = Depends(get_server_state),
    password: str = Form(""),
    confirm_password: str = Form(""),
    admin_username: str = Depends(require_admin_session),
) -> HTMLResponse | RedirectResponse:

    error: str | None = None
    if not password:
        error = "Password is required"
    elif password != confirm_password:
        error = "Passwords do not match"

    if error:
        return templates.TemplateResponse(request, "admin/master_key_set_form.html", _context(error=error))

    try:
        state.app_ctx.kee.pwd_change(password, force_save=True)
    except Exception as e:
        error = f"Database password change failed: {e}"
        return templates.TemplateResponse(request, "admin/master_key_set_form.html", _context(error=error))

    set_master_key(password)  # keep the keyring in sync - only after re-encryption succeeded
    log(f"Database master password changed (Pytre.db re-encrypted) by admin '{admin_username}'", logging.WARNING)
    return RedirectResponse(
        url="/admin/master-key?msg=Database+password+changed", status_code=status.HTTP_303_SEE_OTHER
    )
