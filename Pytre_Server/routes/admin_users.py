"""
Admin webui routes for managing Users - list, create, edit, delete, bulk group
assignment, custom attribute management, and CSV import/export.
Gated by require_admin_session (see admin_routes.py)
"""

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.datastructures import FormData

from Pytre_Core.users import AuthType, OidcLink, User, UserField, hash_password
from Pytre_Server.server_state import ServerState, get_server_state
from Pytre_Server.templating import templates


router = APIRouter(prefix="/users")


class UserForm(BaseModel):
    username: str
    title: str = ""
    admin: bool = False
    auth_type: str
    password: str = ""
    groups: list[str] = Field(default_factory=list)

    async def apply_to(self, user: User, attribs_cust: dict[str, str]) -> None:
        """Copies the submitted values onto `user`.
        Async because if a password is submitted it needs to be hashed (Argon2, deliberately slow) in a
        worker thread, so the event loop stays free. `attribs_cust` are the custom attributes, read by
        the caller from the raw form since their names are only known at runtime - they are merged into
        the user's, the others are kept."""
        user.username = self.username.strip()
        user.title = self.title.strip()
        user.admin = self.admin
        user.auth_type = AuthType.from_value(self.auth_type) or AuthType.NONE
        if self.password:
            user.password_hash = await asyncio.to_thread(hash_password, self.password)
        user.groups = self.groups
        user.attribs_cust.update(attribs_cust)


def _form_context(state: ServerState, user: User) -> dict:
    return {
        "user": user,
        "all_groups": sorted(state.app_ctx.users.groups),
        "all_attribs": sorted(state.app_ctx.users.get_cust_attribs_list()),
        "auth_types": list(AuthType),
        "oidc_providers": sorted(state.app_ctx.oidc_providers.get_all_providers().values(), key=lambda p: p.label),
    }


def _apply_oidc_link(user: User, form_data: FormData) -> None:
    """Applies the submitted provider and sub for the user OIDC link - read directly from the raw form.
    Either field blank -> no link; unchanged issuer+sub -> refresh_token/last_verified preserved;
    any other value (new or different) replaces with a fresh, unverified link."""
    issuer = str(form_data.get("oidc_issuer", "")).strip()
    sub = str(form_data.get("oidc_sub", "")).strip()

    existing = user.oidc_link

    if not issuer or not sub:
        user.oidc_link = None
    elif existing is None or existing.issuer != issuer or existing.sub != sub:
        user.oidc_link = OidcLink(issuer=issuer, sub=sub)


async def _save_user(
    request: Request,
    state: ServerState,
    user: User,
    user_form: UserForm,
) -> HTMLResponse | RedirectResponse:
    """Applies submitted form values onto `user` and saves it. Shared by create and edit -
    `user` is either a blank new User or one just loaded via find_user_by_uuid()."""
    # dynamic custom-attribute fields aren't declared as individual Form(...) params since the
    # set of names is only known at runtime - read directly from the raw form data instead
    form_data = await request.form()
    attribs_cust = {
        name: str(form_data.get(f"attrib__{name}", "")).strip() for name in state.app_ctx.users.get_cust_attribs_list()
    }

    # update user with form submitted values
    await user_form.apply_to(user, attribs_cust)
    _apply_oidc_link(user, form_data)

    try:
        await asyncio.to_thread(user.save)
    except ValueError as e:
        context = _form_context(state, user)
        context["error"] = str(e)
        return templates.TemplateResponse(request, "admin/user_form.html", context)

    return RedirectResponse(url="/admin/users?msg=User+saved", status_code=status.HTTP_303_SEE_OTHER)


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


@router.post("/reload", response_class=RedirectResponse)
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
    user_form: Annotated[UserForm, Form()],
    state: ServerState = Depends(get_server_state),
) -> HTMLResponse | RedirectResponse:
    user = User(state.app_ctx.users, detect_user=False)
    return await _save_user(request, state, user, user_form)


@router.get("/{uuid}/edit", response_class=HTMLResponse, response_model=None)
def user_edit_form(
    uuid: str,
    request: Request,
    state: ServerState = Depends(get_server_state),
) -> HTMLResponse | RedirectResponse:
    user = state.app_ctx.users.find_user_by_uuid(uuid)
    if user is None:
        return RedirectResponse(url="/admin/users?error=User+not+found", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "admin/user_form.html", _form_context(state, user))


@router.post("/{uuid}/edit", response_class=HTMLResponse, response_model=None)
async def user_edit_submit(
    uuid: str,
    request: Request,
    user_form: Annotated[UserForm, Form()],
    state: ServerState = Depends(get_server_state),
) -> HTMLResponse | RedirectResponse:
    user = state.app_ctx.users.find_user_by_uuid(uuid)
    if user is None:
        return RedirectResponse(url="/admin/users?error=User+not+found", status_code=status.HTTP_303_SEE_OTHER)
    return await _save_user(request, state, user, user_form)


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
) -> HTMLResponse | RedirectResponse:
    if not groups:
        context = {
            "mode": "add",
            "uuids": uuids,
            "all_groups": sorted(state.app_ctx.users.groups),
            "error": "No group selected",
        }
        return templates.TemplateResponse(request, "admin/users_groups_form.html", context)

    state.app_ctx.users.users_add_groups(uuids, groups)
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
    request: Request,
    state: ServerState = Depends(get_server_state),
    name: str = Form(...),
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
    request: Request,
    state: ServerState = Depends(get_server_state),
    file: UploadFile = File(...),
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
