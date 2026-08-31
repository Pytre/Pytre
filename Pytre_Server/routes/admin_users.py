"""
Admin webui routes for managing Users - list, create, edit, delete, bulk group
assignment, custom attribute management, and CSV import/export.
Gated by require_admin_session (see admin_routes.py)
"""

import asyncio
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

from Pytre_Core.users import AuthType, User, UserField, hash_password
from Pytre_Server.server_state import ServerState, get_server_state

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

router = APIRouter(prefix="/users")


def _split_new_groups(new_groups: str) -> list[str]:
    """Parses the free-text "add new group(s)" field - comma-separated, blanks dropped."""
    return [g.strip() for g in new_groups.split(",") if g.strip()]


def _form_context(state: ServerState, user: User) -> dict:
    return {
        "user": user,
        "all_groups": sorted(state.app_ctx.users.groups),
        "all_attribs": sorted(state.app_ctx.users.get_cust_attribs_list()),
        "auth_types": list(AuthType),
    }


# ------------------------------------------------------------------------------------------
# Obtain list
# ------------------------------------------------------------------------------------------
@router.get("", response_class=HTMLResponse)
def users_list(
    request: Request,
    state: ServerState = Depends(get_server_state),
    msg: str | None = None,
    error: str | None = None,
) -> HTMLResponse:

    try:
        users = state.app_ctx.users.get_all_users(reload=True)
    except Exception as e:
        users = state.app_ctx.users.get_all_users(reload=False)
        msg = None
        error = f"Load failed: {e}"

    context = {
        "users": users,
        "attribs": sorted(state.app_ctx.users.attribs_cust),
        "msg": msg,
        "error": error,
    }
    return templates.TemplateResponse(request, "admin/users_list.html", context)


@router.post("/reload", response_class=RedirectResponse, response_model=None)
def users_reload() -> RedirectResponse:
    return RedirectResponse(url="/admin/users?msg=Users+reloaded", status_code=status.HTTP_303_SEE_OTHER)


# ------------------------------------------------------------------------------------------
# Create / edit (shared form + save logic)
# ------------------------------------------------------------------------------------------
@router.get("/new", response_class=HTMLResponse)
def user_new_form(request: Request, state: ServerState = Depends(get_server_state)) -> HTMLResponse:
    user = User(state.app_ctx.users, detect_user=False)
    return templates.TemplateResponse(request, "admin/user_form.html", _form_context(state, user))


@router.post("/new", response_class=HTMLResponse, response_model=None)
async def user_new_submit(
    request: Request,
    state: ServerState = Depends(get_server_state),
    username: str = Form(...),
    title: str = Form(""),
    admin: str | None = Form(None),
    msg_login: str = Form(""),
    auth_type: str = Form(...),
    password: str = Form(""),
    groups: list[str] = Form(default_factory=list),
    new_groups: str = Form(""),
) -> HTMLResponse | RedirectResponse:
    user = User(state.app_ctx.users, detect_user=False)
    return await _save_user(
        request, state, user, username, title, admin, msg_login, auth_type, password, groups, new_groups
    )


@router.get("/{uuid}/edit", response_class=HTMLResponse, response_model=None)
def user_edit_form(
    uuid: str, request: Request, state: ServerState = Depends(get_server_state)
) -> HTMLResponse | RedirectResponse:
    user = state.app_ctx.users.find_user_by_uuid(uuid)
    if user is None:
        return RedirectResponse(url="/admin/users?error=User+not+found", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "admin/user_form.html", _form_context(state, user))


@router.post("/{uuid}/edit", response_class=HTMLResponse, response_model=None)
async def user_edit_submit(
    uuid: str,
    request: Request,
    state: ServerState = Depends(get_server_state),
    username: str = Form(...),
    title: str = Form(""),
    admin: str | None = Form(None),
    msg_login: str = Form(""),
    auth_type: str = Form(...),
    password: str = Form(""),
    groups: list[str] = Form(default_factory=list),
    new_groups: str = Form(""),
) -> HTMLResponse | RedirectResponse:
    user = state.app_ctx.users.find_user_by_uuid(uuid)
    if user is None:
        return RedirectResponse(url="/admin/users?error=User+not+found", status_code=status.HTTP_303_SEE_OTHER)
    return await _save_user(
        request, state, user, username, title, admin, msg_login, auth_type, password, groups, new_groups
    )


async def _save_user(
    request: Request,
    state: ServerState,
    user: User,
    username: str,
    title: str,
    admin: str | None,
    msg_login: str,
    auth_type: str,
    password: str,
    groups: list[str],
    new_groups: str,
) -> HTMLResponse | RedirectResponse:
    """Applies submitted form values onto `user` and saves it. Shared by create and edit -
    `user` is either a blank new User or one just loaded via find_user_by_uuid()."""
    user.username = username.strip()
    user.title = title.strip()
    user.admin = admin is not None
    user.msg_login = msg_login.strip()
    user.auth_type = AuthType.from_value(auth_type) or AuthType.NONE
    if password:
        user.password_hash = await asyncio.to_thread(hash_password, password)

    user.groups = list(groups) + _split_new_groups(new_groups)

    # dynamic custom-attribute fields aren't declared as individual Form(...) params since the
    # set of names is only known at runtime - read directly from the raw form data instead
    form_data = await request.form()
    for name in state.app_ctx.users.get_cust_attribs_list():
        user.attribs_cust[name] = str(form_data.get(f"attrib__{name}", "")).strip()

    try:
        await asyncio.to_thread(user.save)
    except ValueError as e:
        context = _form_context(state, user)
        context["error"] = str(e)
        return templates.TemplateResponse(request, "admin/user_form.html", context)

    return RedirectResponse(url="/admin/users?msg=User+saved", status_code=status.HTTP_303_SEE_OTHER)


# ------------------------------------------------------------------------------------------
# Delete
# ------------------------------------------------------------------------------------------
@router.post("/{uuid}/delete")
def user_delete(uuid: str, state: ServerState = Depends(get_server_state)) -> RedirectResponse:
    user = state.app_ctx.users.find_user_by_uuid(uuid)
    if user is None:
        return RedirectResponse(url="/admin/users?error=User+not+found", status_code=status.HTTP_303_SEE_OTHER)

    try:
        user.delete()
    except LookupError as e:
        return RedirectResponse(url=f"/admin/users?error={e}", status_code=status.HTTP_303_SEE_OTHER)

    return RedirectResponse(url="/admin/users?msg=User+deleted", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/delete")
def users_delete_bulk(
    uuids: list[str] = Form(default_factory=list), state: ServerState = Depends(get_server_state)
) -> RedirectResponse:
    """Mirrors UsersWindow._delete_after_msg()'s counted success/failure summary."""
    nb_ok = 0
    nb_errors = 0
    for uuid in uuids:
        user = state.app_ctx.users.find_user_by_uuid(uuid)
        if user is None:
            nb_errors += 1
            continue
        try:
            user.delete()
            nb_ok += 1
        except LookupError:
            nb_errors += 1

    msg = f"{nb_ok}+user(s)+deleted" + (f",+{nb_errors}+error(s)" if nb_errors else "")
    return RedirectResponse(url=f"/admin/users?msg={msg}", status_code=status.HTTP_303_SEE_OTHER)


# ------------------------------------------------------------------------------------------
# Bulk group assignment / removal
# ------------------------------------------------------------------------------------------
@router.get("/groups/add", response_class=HTMLResponse, response_model=None)
def users_groups_add_form(
    request: Request,
    state: ServerState = Depends(get_server_state),
    uuids: list[str] = Query(default_factory=list),
) -> HTMLResponse | RedirectResponse:
    if not uuids:
        return RedirectResponse(url="/admin/users?error=No+user+selected", status_code=status.HTTP_303_SEE_OTHER)
    context = {"mode": "add", "uuids": uuids, "all_groups": sorted(state.app_ctx.users.groups)}
    return templates.TemplateResponse(request, "admin/users_groups_form.html", context)


@router.post("/groups/add", response_class=HTMLResponse, response_model=None)
def users_groups_add_submit(
    request: Request,
    state: ServerState = Depends(get_server_state),
    uuids: list[str] = Form(...),
    groups: list[str] = Form(default_factory=list),
    new_groups: str = Form(""),
) -> HTMLResponse | RedirectResponse:
    groups_to_add = list(groups) + _split_new_groups(new_groups)
    if not groups_to_add:
        context = {
            "mode": "add",
            "uuids": uuids,
            "all_groups": sorted(state.app_ctx.users.groups),
            "error": "No group selected",
        }
        return templates.TemplateResponse(request, "admin/users_groups_form.html", context)

    state.app_ctx.users.users_add_groups(uuids, groups_to_add)
    return RedirectResponse(url="/admin/users?msg=Groups+assigned", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/groups/remove", response_class=HTMLResponse, response_model=None)
def users_groups_remove_form(
    request: Request,
    state: ServerState = Depends(get_server_state),
    uuids: list[str] = Query(default_factory=list),
) -> HTMLResponse | RedirectResponse:
    if not uuids:
        return RedirectResponse(url="/admin/users?error=No+user+selected", status_code=status.HTTP_303_SEE_OTHER)
    context = {"mode": "remove", "uuids": uuids, "all_groups": sorted(state.app_ctx.users.groups)}
    return templates.TemplateResponse(request, "admin/users_groups_form.html", context)


@router.post("/groups/remove", response_class=HTMLResponse, response_model=None)
def users_groups_remove_submit(
    request: Request,
    state: ServerState = Depends(get_server_state),
    uuids: list[str] = Form(...),
    groups: list[str] = Form(default_factory=list),
) -> HTMLResponse | RedirectResponse:
    if not groups:
        context = {
            "mode": "remove",
            "uuids": uuids,
            "all_groups": sorted(state.app_ctx.users.groups),
            "error": "No group selected",
        }
        return templates.TemplateResponse(request, "admin/users_groups_form.html", context)

    state.app_ctx.users.users_remove_groups(uuids, groups)
    return RedirectResponse(url="/admin/users?msg=Groups+removed", status_code=status.HTTP_303_SEE_OTHER)


# ------------------------------------------------------------------------------------------
# Custom attribute management (add/remove a column across every user)
# ------------------------------------------------------------------------------------------
@router.get("/attributes", response_class=HTMLResponse)
def users_attributes_page(request: Request, state: ServerState = Depends(get_server_state)) -> HTMLResponse:
    context = {"attribs": sorted(state.app_ctx.users.get_cust_attribs_list())}
    return templates.TemplateResponse(request, "admin/users_attributes.html", context)


@router.post("/attributes/add", response_class=HTMLResponse, response_model=None)
def users_attributes_add(
    request: Request, state: ServerState = Depends(get_server_state), name: str = Form(...)
) -> HTMLResponse | RedirectResponse:
    name = name.strip()
    attribs = state.app_ctx.users.get_cust_attribs_list()

    error: str | None = None
    if not name:
        error = "Attribute name is required"
    elif name.lower() in UserField.reserved_keys():
        error = f'"{name}" is reserved internally'
    elif any(name.lower() == a.lower() for a in attribs):
        error = f'"{name}" already exists'

    if error:
        return templates.TemplateResponse(request, "admin/users_attributes.html", {"attribs": attribs, "error": error})

    state.app_ctx.users.modify_custom_attribs([name])
    return RedirectResponse(url="/admin/users?msg=Attribute+added", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/attributes/remove", response_class=HTMLResponse, response_model=None)
def users_attributes_remove(
    request: Request,
    state: ServerState = Depends(get_server_state),
    names: list[str] = Form(default_factory=list),
) -> HTMLResponse | RedirectResponse:
    if not names:
        attribs = state.app_ctx.users.get_cust_attribs_list()
        context = {"attribs": attribs, "error": "No attribute selected"}
        return templates.TemplateResponse(request, "admin/users_attributes.html", context)

    state.app_ctx.users.remove_custom_attribs(names)
    return RedirectResponse(url="/admin/users?msg=Attribute(s)+removed", status_code=status.HTTP_303_SEE_OTHER)


# ------------------------------------------------------------------------------------------
# CSV import / export
# ------------------------------------------------------------------------------------------
@router.get("/import", response_class=HTMLResponse)
def users_import_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "admin/users_import.html")


@router.post("/import", response_class=HTMLResponse, response_model=None)
async def users_import_submit(
    request: Request, state: ServerState = Depends(get_server_state), file: UploadFile = File(...)
) -> HTMLResponse | RedirectResponse:
    tmp_path: str | None = None
    try:
        content = await file.read()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        await asyncio.to_thread(state.app_ctx.users.csv_import, tmp_path)
    except Exception as e:
        return templates.TemplateResponse(request, "admin/users_import.html", {"error": str(e)})
    finally:
        if tmp_path:
            os.unlink(tmp_path)

    return RedirectResponse(url="/admin/users?msg=Users+imported", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/export", response_model=None)
def users_export(state: ServerState = Depends(get_server_state)) -> FileResponse | RedirectResponse:
    fd, tmp_path_str = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    tmp_path = Path(tmp_path_str)

    try:
        state.app_ctx.users.csv_export(tmp_path, overwrite=True, pwd_infos=True)
    except Exception:
        os.unlink(tmp_path)
        return RedirectResponse(url="/admin/users?error=Export+failed", status_code=status.HTTP_303_SEE_OTHER)

    return FileResponse(
        tmp_path,
        media_type="text/csv",
        filename="users_export.csv",
        background=BackgroundTask(os.unlink, tmp_path),
    )
