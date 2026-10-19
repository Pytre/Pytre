"""
Admin webui routes for the query catalog - list queries, reload the catalog from disk, and
manage a temporary exemption from the template_hash staleness check.
Gated by require_admin_session (see admin_router.py)
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from Pytre_Core.context import UserContext
from Pytre_Core.sql_query.file_parser import FileParser
from Pytre_Core.utils import log as _log
from Pytre_Server.query_catalog import QueryCatalog
from Pytre_Server.routes.admin_auth import require_admin_session
from Pytre_Server.server_state import ServerState, get_server_state
from Pytre_Server.templating import templates


router = APIRouter(prefix="/queries")


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


@dataclass
class _QueryRow:
    """Display-only row for the queries list page - built straight from a FileParser's header"""

    file_id: str
    name: str
    description: str
    servers: list[str]
    groups: list[str]
    hidden: bool
    exempt_until: datetime | None  # UTC datetime


def _build_query_row(parser: FileParser, ignore_hash_until: dict[str, datetime]) -> _QueryRow:
    infos = parser.extract_header_infos()
    file_id = parser.filename.stem
    exemption = ignore_hash_until.get(file_id)
    now = datetime.now(timezone.utc)

    return _QueryRow(
        file_id=file_id,
        name=infos.get("code", file_id),
        description=infos.get("description", ""),
        servers=infos.get("servers", []),
        groups=infos.get("grp_authorized", []),
        hidden=infos.get("hide", "0") != "0",
        exempt_until=exemption if exemption is not None and exemption > now else None,
    )


def _clear_hash_exemption(state: ServerState, file_id: str, admin_username: str) -> RedirectResponse:
    if state.resources.ignore_hash_until.pop(file_id, None) is not None:
        log(f"Hash-check exemption for '{file_id}' cleared by admin '{admin_username}'", logging.WARNING)

    msg = quote_plus(f"Hash exemption for {file_id} cleared")
    return RedirectResponse(url=f"/admin/queries?msg={msg}", status_code=status.HTTP_303_SEE_OTHER)


# ------------------------------------------------------------------------------------------
# Obtain list
# ------------------------------------------------------------------------------------------
@router.get("", response_class=HTMLResponse)
def queries_list(
    request: Request,
    state: ServerState = Depends(get_server_state),
    msg: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    rows = [_build_query_row(p, state.resources.ignore_hash_until) for p in state.query_catalog.parsers]
    context = {
        "rows": rows,
        "load_errors": state.query_catalog.load_errors + state.query_catalog.build_errors,
        "msg": msg,
        "error": error,
    }
    return templates.TemplateResponse(request, "admin/queries_list.html", context)


@router.post("/reload", response_class=RedirectResponse)
def queries_reload(
    request: Request,
    admin_username: str = Depends(require_admin_session),
    state: ServerState = Depends(get_server_state),
) -> RedirectResponse:
    """Rebuilds the query catalog and swaps app.state.server for a new ServerState reusing the
    same resources (drivers, ignore_hash_until...) - never mutated in place, so a request already
    in flight keeps a consistent view of whichever catalog it started with (see QueryCatalog).

    Also builds a Query from every parser using the reloading admin's own identity to surface
    structurally broken queries (bad param/template syntax...) - a broken query just silently
    disappears from every user's /queries list, with nothing shown anywhere."""
    try:
        new_catalog = QueryCatalog.load(state.app_ctx.settings.queries_folder)
    except Exception as e:
        msg = quote_plus(f"Reload failed: {e}")
        return RedirectResponse(url=f"/admin/queries?error={msg}", status_code=status.HTTP_303_SEE_OTHER)

    admin_ctx = UserContext(state.app_ctx.users, username=admin_username)
    new_catalog = new_catalog.with_build_errors(state.app_ctx, admin_ctx)

    request.app.state.server = ServerState(
        app_ctx=state.app_ctx,
        query_catalog=new_catalog,
        chunk_size=state.chunk_size,
        max_concurrency_per_user=state.max_concurrency_per_user,
        heartbeat_secs=state.heartbeat_secs,
        resources=state.resources,
    )

    total_errors = new_catalog.load_errors + new_catalog.build_errors
    msg = f"Queries reloaded ({len(new_catalog.parsers)} loaded"
    msg += f", {len(total_errors)} error(s)" if total_errors else ""
    msg += ")"
    return RedirectResponse(url=f"/admin/queries?msg={quote_plus(msg)}", status_code=status.HTTP_303_SEE_OTHER)


# ------------------------------------------------------------------------------------------
# Edit
# ------------------------------------------------------------------------------------------
@router.get("/{file_id}/edit", response_class=HTMLResponse, response_model=None)
def query_edit_form(
    file_id: str, request: Request, state: ServerState = Depends(get_server_state)
) -> HTMLResponse | RedirectResponse:
    parser = state.query_catalog.by_file_id.get(file_id)
    if parser is None:
        return RedirectResponse(url="/admin/queries?error=Query+not+found", status_code=status.HTTP_303_SEE_OTHER)

    row = _build_query_row(parser, state.resources.ignore_hash_until)
    context = {
        "row": row,
        "until_ts": int(row.exempt_until.timestamp()) if row.exempt_until else "",
        "server_now_ts": int(datetime.now(timezone.utc).timestamp()),
    }
    return templates.TemplateResponse(request, "admin/query_form.html", context)


@router.post("/{file_id}/ignore-hash")
def query_ignore_hash_set(
    file_id: str,
    state: ServerState = Depends(get_server_state),
    until_ts: str = Form(""),
    admin_username: str = Depends(require_admin_session),
) -> RedirectResponse:
    """`until_ts` is the epoch (UTC seconds) the browser computed and displayed - applied as-is,
    so what the webui displayed is exactly what gets stored, however long it took to hit Save.
    Empty clears the exemption (the cross button, or the hours delta bringing it to the past)."""

    if file_id not in state.query_catalog.by_file_id:
        return RedirectResponse(url="/admin/queries?error=Query+not+found", status_code=status.HTTP_303_SEE_OTHER)

    if not until_ts:
        return _clear_hash_exemption(state, file_id, admin_username)

    # Convert until_ts to UTC datetime and remove exemption if it's in the past
    try:
        new_datetime = datetime.fromtimestamp(int(until_ts), tz=timezone.utc)
        now = datetime.now(timezone.utc)
        if new_datetime <= now:
            return _clear_hash_exemption(state, file_id, admin_username)
    except (ValueError, OverflowError, OSError):
        return RedirectResponse(url="/admin/queries?error=Invalid+date", status_code=status.HTTP_303_SEE_OTHER)

    # Check if new exemption is different from current to decide if it needs to be saved
    current_datetime = state.resources.ignore_hash_until.get(file_id)
    if current_datetime is None or int(new_datetime.timestamp()) != int(current_datetime.timestamp()):
        msg = f"Hash check for {file_id} updated"
        state.resources.ignore_hash_until[file_id] = new_datetime
        log(
            f"Hash-check exemption for '{file_id}' set until {new_datetime.isoformat()} by admin '{admin_username}'",
            logging.WARNING,
        )
    else:
        msg = f"Hash exemption for {file_id} unchanged"

    return RedirectResponse(url=f"/admin/queries?msg={quote_plus(msg)}", status_code=status.HTTP_303_SEE_OTHER)
