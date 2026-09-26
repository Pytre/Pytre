"""
Admin webui routes for managing Servers - list, create, edit, delete, and CSV import/export.
Gated by require_admin_session (see admin_routes.py)
"""

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Annotated
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from Pytre_Core.servers import Server, ServerType
from Pytre_Server.server_state import ServerState, get_server_state
from Pytre_Server.templating import templates


router = APIRouter(prefix="/servers")


class ServerForm(BaseModel):
    id: str
    description: str = ""
    login: str = ""
    password: str = ""
    type: str
    charset: str = ""
    database: str = ""
    host: str = ""
    port: str = ""
    server: str = ""
    login_timeout: int
    timeout: int
    trust_server_certificate: bool = False
    groups: list[str] = Field(default_factory=list)

    def apply_to(self, server: Server) -> None:
        """Copies the submitted values onto `server`."""
        server.id = self.id.strip()
        server.description = self.description.strip()
        server.user = self.login.strip()
        if self.password:
            server.password = self.password
        server.type = ServerType(self.type).name if self.type in ServerType else ServerType.mssql.name
        server.charset = self.charset.strip()
        server.database = self.database.strip()
        server.host = self.host.strip()
        server.port = self.port.strip()
        server.server = self.server.strip()
        server.login_timeout = self.login_timeout
        server.timeout = self.timeout
        server.trust_server_certificate = self.trust_server_certificate
        server.grp_authorized = self.groups


def _form_context(state: ServerState, server: Server) -> dict:
    return {
        "server": server,
        "server_types": list(ServerType),
        "all_groups": sorted(state.app_ctx.servers.groups),
    }


async def _reload_servers(state: ServerState) -> int:
    await asyncio.to_thread(state.app_ctx.servers.get_all_servers, reload=True)
    reset_count = await state.resources.reset_connections()

    return reset_count


# ------------------------------------------------------------------------------------------
# Obtain list
# ------------------------------------------------------------------------------------------
@router.get("", response_class=HTMLResponse)
def servers_list(
    request: Request,
    state: ServerState = Depends(get_server_state),
    msg: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    servers = state.app_ctx.servers.get_all_servers(reload=False)
    context = {
        "servers": servers,
        "msg": msg,
        "error": error,
    }
    return templates.TemplateResponse(request, "admin/servers_list.html", context)


@router.post("/reload", response_class=RedirectResponse)
async def servers_reload(state: ServerState = Depends(get_server_state)) -> RedirectResponse:
    try:
        reset_count = await _reload_servers(state)
    except Exception as e:
        msg = f"Reload failed: {quote_plus(str(e))}"
        return RedirectResponse(url=f"/admin/servers?error={msg}", status_code=status.HTTP_303_SEE_OTHER)

    msg = f"Servers reloaded ({reset_count} connection(s) reset)"
    return RedirectResponse(url=f"/admin/servers?msg={msg}", status_code=status.HTTP_303_SEE_OTHER)


# ------------------------------------------------------------------------------------------
# Create / edit (shared form + save logic)
# ------------------------------------------------------------------------------------------
@router.get("/new", response_class=HTMLResponse)
def server_new_form(request: Request, state: ServerState = Depends(get_server_state)) -> HTMLResponse:
    server = Server(state.app_ctx.servers)
    return templates.TemplateResponse(request, "admin/server_form.html", _form_context(state, server))


@router.post("/new", response_class=HTMLResponse, response_model=None)
async def server_new_submit(
    request: Request,
    server_form: Annotated[ServerForm, Form()],
    state: ServerState = Depends(get_server_state),
) -> HTMLResponse | RedirectResponse:
    server_obj = Server(state.app_ctx.servers)
    return await _save_server(request, state, server_obj, server_form)


@router.get("/{uuid}/edit", response_class=HTMLResponse, response_model=None)
def server_edit_form(
    uuid: str, request: Request, state: ServerState = Depends(get_server_state)
) -> HTMLResponse | RedirectResponse:
    server = state.app_ctx.servers.find_server_by_uuid(uuid)
    if server is None:
        return RedirectResponse(url="/admin/servers?error=Server+not+found", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "admin/server_form.html", _form_context(state, server))


@router.post("/{uuid}/edit", response_class=HTMLResponse, response_model=None)
async def server_edit_submit(
    uuid: str,
    request: Request,
    server_form: Annotated[ServerForm, Form()],
    state: ServerState = Depends(get_server_state),
) -> HTMLResponse | RedirectResponse:
    server_obj = state.app_ctx.servers.find_server_by_uuid(uuid)
    if server_obj is None:
        return RedirectResponse(url="/admin/servers?error=Server+not+found", status_code=status.HTTP_303_SEE_OTHER)
    return await _save_server(request, state, server_obj, server_form)


async def _save_server(
    request: Request, state: ServerState, server_obj: Server, server_form: ServerForm
) -> HTMLResponse | RedirectResponse:
    """Applies submitted form values onto `server_obj` and saves it. Shared by create and edit -
    `server_obj` is either a blank new Server or one just loaded via find_server_by_uuid()."""
    server_form.apply_to(server_obj)

    try:
        await asyncio.to_thread(server_obj.save)
    except ValueError as e:
        context = _form_context(state, server_obj)
        context["error"] = str(e)
        return templates.TemplateResponse(request, "admin/server_form.html", context)

    await state.resources.reset_connections(server_obj.id)
    return RedirectResponse(url="/admin/servers?msg=Server+saved", status_code=status.HTTP_303_SEE_OTHER)


# ------------------------------------------------------------------------------------------
# Delete
# ------------------------------------------------------------------------------------------
@router.post("/{uuid}/delete")
async def server_delete(uuid: str, state: ServerState = Depends(get_server_state)) -> RedirectResponse:
    server = state.app_ctx.servers.find_server_by_uuid(uuid)
    if server is None:
        return RedirectResponse(url="/admin/servers?error=Server+not+found", status_code=status.HTTP_303_SEE_OTHER)

    try:
        await asyncio.to_thread(server.delete)
    except LookupError as e:
        return RedirectResponse(url=f"/admin/servers?error={e}", status_code=status.HTTP_303_SEE_OTHER)

    await state.resources.reset_connections(server.id)
    return RedirectResponse(url="/admin/servers?msg=Server+deleted", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/delete")
async def servers_delete_bulk(
    uuids: list[str] = Form(default_factory=list), state: ServerState = Depends(get_server_state)
) -> RedirectResponse:
    """Mirrors ServersWindow._delete_after_msg()'s counted success/failure summary."""
    nb_ok = 0
    nb_errors = 0
    for uuid in uuids:
        server = state.app_ctx.servers.find_server_by_uuid(uuid)
        if server is None:
            nb_errors += 1
            continue
        try:
            await asyncio.to_thread(server.delete)
            await state.resources.reset_connections(server.id)
            nb_ok += 1
        except LookupError:
            nb_errors += 1

    msg = f"{nb_ok}+server(s)+deleted" + (f",+{nb_errors}+error(s)" if nb_errors else "")
    return RedirectResponse(url=f"/admin/servers?msg={msg}", status_code=303)


# ------------------------------------------------------------------------------------------
# Bulk group assignment / removal
# ------------------------------------------------------------------------------------------
@router.get("/groups/add", response_class=HTMLResponse, response_model=None)
def servers_groups_add_form(
    request: Request,
    state: ServerState = Depends(get_server_state),
    uuids: list[str] = Query(default_factory=list),
) -> HTMLResponse | RedirectResponse:
    if not uuids:
        return RedirectResponse(url="/admin/servers?error=No+server+selected", status_code=status.HTTP_303_SEE_OTHER)
    context = {"mode": "add", "uuids": uuids, "all_groups": sorted(state.app_ctx.servers.groups)}
    return templates.TemplateResponse(request, "admin/servers_groups_form.html", context)


@router.post("/groups/add", response_class=HTMLResponse, response_model=None)
def servers_groups_add_submit(
    request: Request,
    state: ServerState = Depends(get_server_state),
    uuids: list[str] = Form(...),
    groups: list[str] = Form(default_factory=list),
) -> HTMLResponse | RedirectResponse:
    if not groups:
        context = {
            "mode": "add",
            "uuids": uuids,
            "all_groups": sorted(state.app_ctx.servers.groups),
            "error": "No group selected",
        }
        return templates.TemplateResponse(request, "admin/servers_groups_form.html", context)

    state.app_ctx.servers.servers_add_groups(uuids, groups)
    return RedirectResponse(url="/admin/servers?msg=Groups+assigned", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/groups/remove", response_class=HTMLResponse, response_model=None)
def servers_groups_remove_form(
    request: Request,
    state: ServerState = Depends(get_server_state),
    uuids: list[str] = Query(default_factory=list),
) -> HTMLResponse | RedirectResponse:
    if not uuids:
        return RedirectResponse(url="/admin/servers?error=No+server+selected", status_code=status.HTTP_303_SEE_OTHER)
    context = {"mode": "remove", "uuids": uuids, "all_groups": sorted(state.app_ctx.servers.groups)}
    return templates.TemplateResponse(request, "admin/servers_groups_form.html", context)


@router.post("/groups/remove", response_class=HTMLResponse, response_model=None)
def servers_groups_remove_submit(
    request: Request,
    state: ServerState = Depends(get_server_state),
    uuids: list[str] = Form(...),
    groups: list[str] = Form(default_factory=list),
) -> HTMLResponse | RedirectResponse:
    if not groups:
        context = {
            "mode": "remove",
            "uuids": uuids,
            "all_groups": sorted(state.app_ctx.servers.groups),
            "error": "No group selected",
        }
        return templates.TemplateResponse(request, "admin/servers_groups_form.html", context)

    state.app_ctx.servers.servers_remove_groups(uuids, groups)
    return RedirectResponse(url="/admin/servers?msg=Groups+removed", status_code=status.HTTP_303_SEE_OTHER)


# ------------------------------------------------------------------------------------------
# CSV import / export
# ------------------------------------------------------------------------------------------
@router.get("/import", response_class=HTMLResponse)
def servers_import_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "admin/servers_import.html")


@router.post("/import", response_class=HTMLResponse, response_model=None)
async def servers_import_submit(
    request: Request, state: ServerState = Depends(get_server_state), file: UploadFile = File(...)
) -> HTMLResponse | RedirectResponse:
    tmp_path: str | None = None
    try:
        content = await file.read()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        await asyncio.to_thread(state.app_ctx.servers.csv_import, tmp_path)
    except Exception as e:
        return templates.TemplateResponse(request, "admin/servers_import.html", {"error": str(e)})
    finally:
        if tmp_path:
            os.unlink(tmp_path)

    return RedirectResponse(url="/admin/servers?msg=Servers+imported", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/export", response_model=None)
def servers_export(state: ServerState = Depends(get_server_state)) -> FileResponse | RedirectResponse:
    fd, tmp_path_str = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    tmp_path = Path(tmp_path_str)

    try:
        state.app_ctx.servers.csv_export(tmp_path, overwrite=True)
    except Exception:
        os.unlink(tmp_path)
        return RedirectResponse(url="/admin/servers?error=Export+failed", status_code=status.HTTP_303_SEE_OTHER)

    return FileResponse(
        tmp_path,
        media_type="text/csv",
        filename="servers_export.csv",
        background=BackgroundTask(os.unlink, tmp_path),
    )
