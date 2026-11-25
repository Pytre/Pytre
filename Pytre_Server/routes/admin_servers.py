"""
Admin webui routes for managing Servers - list, create, edit, delete, and CSV import/export.
Gated by require_admin_session (see admin_routes.py)
"""

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Annotated
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from Pytre_Core.servers import DEFAULT_MAX_CONCURRENCY, Server, ServerTarget, ServerType
from Pytre_Server.audit import AuditedRecords, audit, changes_between, shorten
from Pytre_Server.routes.admin_auth import is_loopback, require_admin_session
from Pytre_Server.server_state import client_host, get_server_state, ServerState
from Pytre_Server.templating import templates


TARGET_FIELDS = ("type", "host", "port", "server", "trust_certificate")  # fields of Server.audit_fields()
SERVERS_AUDIT = AuditedRecords(
    "server",
    "Server",
    "id",
    Server.AUDIT_SECRETS,
    warns=lambda before, after: before is not None and any(before[f] != after[f] for f in TARGET_FIELDS),
)  # a changed target gets the stored SQL credentials

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
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    trust_certificate: bool = False
    groups: list[str] = Field(default_factory=list)

    @property
    def server_type(self) -> str:
        return ServerType(self.type).name if self.type in ServerType else ServerType.mssql.name

    @property
    def target(self) -> ServerTarget:
        """The target this form would give the server, see apply_to()."""
        return ServerTarget.of(self.server_type, self.host, self.port, self.server, self.trust_certificate)

    def apply_to(self, server: Server) -> None:
        """Copies the submitted values onto `server`."""
        server.id = self.id.strip()
        server.description = self.description.strip()
        server.user = self.login.strip()
        if self.password:
            server.password = self.password
        server.type = self.server_type
        server.charset = self.charset.strip()
        server.database = self.database.strip()
        server.host = self.host.strip()
        server.port = self.port.strip()
        server.server = self.server.strip()
        server.login_timeout = self.login_timeout
        server.timeout = self.timeout
        server.max_concurrency = self.max_concurrency
        server.trust_certificate = self.trust_certificate
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


async def _apply_saved_servers(state: ServerState, server_id: str | None = None) -> None:
    """Applies servers just saved: the in-memory list is rebuilt from the entries, then the drivers are reset, for
    `server_id` or of every server, so the next executions use the new values. Running ones end as they began."""
    await asyncio.to_thread(state.app_ctx.servers.get_all_servers)
    await state.resources.reset_connections(server_id)


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
        "is_local": is_loopback(request),  # only to choose what message the export confirmation should show
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
    admin_username: str = Depends(require_admin_session),
) -> HTMLResponse | RedirectResponse:
    server_obj = Server(state.app_ctx.servers)
    return await _save_server(request, state, server_obj, server_form, admin_username)


@router.get("/{uuid}/edit", response_class=HTMLResponse, response_model=None)
def server_edit_form(
    uuid: str,
    request: Request,
    state: ServerState = Depends(get_server_state),
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
    admin_username: str = Depends(require_admin_session),
) -> HTMLResponse | RedirectResponse:
    server_obj = state.app_ctx.servers.find_server_by_uuid(uuid)
    if server_obj is None:
        return RedirectResponse(url="/admin/servers?error=Server+not+found", status_code=status.HTTP_303_SEE_OTHER)
    return await _save_server(request, state, server_obj, server_form, admin_username)


async def _save_server(
    request: Request, state: ServerState, server_obj: Server, server_form: ServerForm, admin_username: str
) -> HTMLResponse | RedirectResponse:
    """Applies submitted form values onto `server_obj` and saves it. Shared by create and edit -
    `server_obj` is either a blank new Server or one just loaded via find_server_by_uuid().
    An existing server whose target changes needs its password again: never send a stored password to a new target."""

    target_changed = bool(server_obj.uuid) and server_form.target != server_obj.target  # before apply_to()
    before = server_obj.audit_fields() if server_obj.uuid else None

    server_form.apply_to(server_obj)

    if target_changed and not server_form.password:
        assert before is not None
        msg = f"Target change of server {shorten(before['id'])!r} without its password "
        msg += f"refused for admin {admin_username!r}"
        changes = changes_between(before, server_obj.audit_fields(), Server.AUDIT_SECRETS)
        audit(
            "server.update.failed",
            client_host(request),
            msg,
            actor=admin_username,
            target=before["id"],
            changes=changes,
            level=logging.WARNING,
        )

        context = _form_context(state, server_obj)
        context["error"] = (
            "Changing the host, port, instance or type of a server, or whether its certificate is checked, requires "
            "entering its password again."
        )
        return templates.TemplateResponse(request, "admin/server_form.html", context)

    saved_uuid = server_obj.uuid
    try:
        await asyncio.to_thread(server_obj.save)
    except (ValueError, OSError) as e:
        server_obj.uuid = saved_uuid  # a failed creation must be displayed as "new" (its entry was discarded)
        context = _form_context(state, server_obj)
        context["error"] = str(e)
        return templates.TemplateResponse(request, "admin/server_form.html", context)

    SERVERS_AUDIT.log_change(client_host(request), admin_username, before, server_obj.audit_fields())

    await _apply_saved_servers(state, server_obj.id)
    return RedirectResponse(url="/admin/servers?msg=Server+saved", status_code=status.HTTP_303_SEE_OTHER)


# ------------------------------------------------------------------------------------------
# Delete
# ------------------------------------------------------------------------------------------
@router.post("/{uuid}/delete")
async def server_delete(
    uuid: str,
    request: Request,
    state: ServerState = Depends(get_server_state),
    admin_username: str = Depends(require_admin_session),
) -> RedirectResponse:
    server = state.app_ctx.servers.find_server_by_uuid(uuid)
    if server is None:
        return RedirectResponse(url="/admin/servers?error=Server+not+found", status_code=status.HTTP_303_SEE_OTHER)

    before = server.audit_fields()

    try:
        await asyncio.to_thread(server.delete)
    except LookupError as e:
        return RedirectResponse(url=f"/admin/servers?error={e}", status_code=status.HTTP_303_SEE_OTHER)

    SERVERS_AUDIT.log_change(client_host(request), admin_username, before, None)

    await state.resources.reset_connections(server.id)
    return RedirectResponse(url="/admin/servers?msg=Server+deleted", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/delete")
async def servers_delete_bulk(
    request: Request,
    uuids: list[str] = Form(default_factory=list),
    state: ServerState = Depends(get_server_state),
    admin_username: str = Depends(require_admin_session),
) -> RedirectResponse:
    nb_ok = 0
    nb_errors = 0
    for uuid in uuids:
        server = state.app_ctx.servers.find_server_by_uuid(uuid)
        if server is None:
            nb_errors += 1
            continue
        try:
            before = server.audit_fields()

            await asyncio.to_thread(server.delete)

            SERVERS_AUDIT.log_change(client_host(request), admin_username, before, None)

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
    admin_username: str = Depends(require_admin_session),
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

    before_all = state.app_ctx.servers.audit_snapshot()
    state.app_ctx.servers.servers_add_groups(uuids, groups)
    after_all = state.app_ctx.servers.audit_snapshot()

    SERVERS_AUDIT.log_changes(client_host(request), admin_username, before_all, after_all, "groups added")

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
    admin_username: str = Depends(require_admin_session),
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

    before_all = state.app_ctx.servers.audit_snapshot()
    state.app_ctx.servers.servers_remove_groups(uuids, groups)
    after_all = state.app_ctx.servers.audit_snapshot()

    SERVERS_AUDIT.log_changes(client_host(request), admin_username, before_all, after_all, "groups removed")

    return RedirectResponse(url="/admin/servers?msg=Groups+removed", status_code=status.HTTP_303_SEE_OTHER)


# ------------------------------------------------------------------------------------------
# CSV import / export
# ------------------------------------------------------------------------------------------
@router.get("/import", response_class=HTMLResponse)
def servers_import_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "admin/servers_import.html")


@router.post("/import", response_class=HTMLResponse, response_model=None)
async def servers_import_submit(
    request: Request,
    state: ServerState = Depends(get_server_state),
    admin_username: str = Depends(require_admin_session),
    file: UploadFile = File(...),
) -> HTMLResponse | RedirectResponse:

    tmp_path: str | None = None
    try:
        content = await file.read()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        before_all = await asyncio.to_thread(state.app_ctx.servers.audit_snapshot)
        await asyncio.to_thread(state.app_ctx.servers.csv_import, tmp_path)
    except Exception as e:
        return templates.TemplateResponse(request, "admin/servers_import.html", {"error": str(e)})
    finally:
        if tmp_path:
            os.unlink(tmp_path)

    after_all = await asyncio.to_thread(state.app_ctx.servers.audit_snapshot)
    counts = SERVERS_AUDIT.log_changes(client_host(request), admin_username, before_all, after_all, "CSV import")
    msg = (
        f"Servers imported by admin {admin_username!r} "
        f"({counts['server.create']} created, {counts['server.update']} updated)"
    )
    audit("server.import", client_host(request), msg, actor=admin_username)

    await _apply_saved_servers(state)  # every server: any may have been changed
    return RedirectResponse(url="/admin/servers?msg=Servers+imported", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/export", response_model=None)
def servers_export(
    request: Request,
    state: ServerState = Depends(get_server_state),
    admin_username: str = Depends(require_admin_session),
) -> FileResponse | RedirectResponse:
    """Passwords are only included for a request from the server itself (see is_loopback)."""
    include_pwds = is_loopback(request)

    fd, tmp_path_str = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    tmp_path = Path(tmp_path_str)

    try:
        state.app_ctx.servers.csv_export(tmp_path, overwrite=True, with_passwords=include_pwds)
    except Exception:
        os.unlink(tmp_path)
        return RedirectResponse(url="/admin/servers?error=Export+failed", status_code=status.HTTP_303_SEE_OTHER)

    if include_pwds:
        msg, level = f"Servers exported with their passwords by admin {admin_username!r}", logging.WARNING
    else:
        msg, level = f"Servers exported without their passwords by admin {admin_username!r}", logging.INFO

    audit("server.export", client_host(request), msg, actor=admin_username, level=level)

    return FileResponse(
        tmp_path,
        media_type="text/csv",
        filename="servers_export.csv",
        background=BackgroundTask(os.unlink, tmp_path),
    )
