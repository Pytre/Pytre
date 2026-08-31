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

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from Pytre_Server.master_key import delete_master_key, get_master_key, set_master_key
from Pytre_Server.server_state import ServerState, get_server_state


_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

router = APIRouter(prefix="/master-key")


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


@router.post("/reveal", response_model=None)
def master_key_reveal() -> dict[str, str | None]:
    return {"value": get_master_key()}


@router.post("/delete")
def master_key_delete() -> RedirectResponse:
    delete_master_key()
    return RedirectResponse(url="/admin/master-key?msg=Master+key+deleted", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/set", response_class=HTMLResponse, response_model=None)
def master_key_set(
    request: Request, password: str = Form(""), confirm_password: str = Form("")
) -> HTMLResponse | RedirectResponse:

    error: str | None = None
    if not password:
        error = "Password is required"
    elif password != confirm_password:
        error = "Passwords do not match"

    if error:
        return templates.TemplateResponse(request, "admin/master_key_set_form.html", _context(error=error))

    set_master_key(password)
    return RedirectResponse(url="/admin/master-key?msg=Master+key+saved", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/change-db-password", response_class=HTMLResponse, response_model=None)
def master_key_change_db_password(
    request: Request,
    state: ServerState = Depends(get_server_state),
    password: str = Form(""),
    confirm_password: str = Form(""),
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
    return RedirectResponse(
        url="/admin/master-key?msg=Database+password+changed", status_code=status.HTTP_303_SEE_OTHER
    )
