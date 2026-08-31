"""
Admin webui routes for the query catalog - list queries, reload the catalog from disk, and
manage a temporary exemption from the template_hash staleness check.
Gated by require_admin_session (see admin_router.py)
"""

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from Pytre_Core.sql_query.file_parser import FileParser
from Pytre_Server.query_catalog import QueryCatalog
from Pytre_Server.server_state import ServerState, get_server_state

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

router = APIRouter(prefix="/queries")


@dataclass
class _QueryRow:
    """Display-only row for the queries list page - built straight from a FileParser's header"""

    file_id: str
    name: str
    description: str
    servers: list[str]
    groups: list[str]
    hidden: bool
    exempt_until: date | None


def _build_row(parser: FileParser, ignore_hash_until: dict[str, datetime]) -> _QueryRow:
    infos = parser.extract_header_infos()
    file_id = parser.filename.stem
    exemption = ignore_hash_until.get(file_id)

    return _QueryRow(
        file_id=file_id,
        name=infos.get("code", file_id),
        description=infos.get("description", ""),
        servers=infos.get("servers", []),
        groups=infos.get("grp_authorized", []),
        hidden=infos.get("hide", "0") != "0",
        exempt_until=exemption.date() if exemption else None,
    )


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
    rows = [_build_row(p, state.resources.ignore_hash_until) for p in state.query_catalog.parsers]
    context = {
        "rows": rows,
        "load_errors": state.query_catalog.load_errors,
        "msg": msg,
        "error": error,
        "today": date.today(),
    }
    return templates.TemplateResponse(request, "admin/queries_list.html", context)


@router.post("/reload", response_class=RedirectResponse, response_model=None)
def queries_reload(request: Request, state: ServerState = Depends(get_server_state)) -> RedirectResponse:
    """Rebuilds the query catalog and swaps app.state.server for a new ServerState reusing the
    same resources (drivers, ignore_hash_until...) - never mutated in place, so a request already
    in flight keeps a consistent view of whichever catalog it started with (see QueryCatalog)."""
    try:
        new_catalog = QueryCatalog.load(state.app_ctx.settings.queries_folder)
    except Exception as e:
        msg = f"Reload failed: {quote_plus(str(e))}"
        return RedirectResponse(url=f"/admin/queries?error={msg}", status_code=status.HTTP_303_SEE_OTHER)

    request.app.state.server = ServerState(
        app_ctx=state.app_ctx,
        query_catalog=new_catalog,
        chunk_size=state.chunk_size,
        max_concurrency_per_user=state.max_concurrency_per_user,
        resources=state.resources,
    )

    msg = f"Queries reloaded ({len(new_catalog.parsers)} loaded"
    msg += f", {len(new_catalog.load_errors)} error(s)" if new_catalog.load_errors else ""
    msg += ")"
    return RedirectResponse(url=f"/admin/queries?msg={msg}", status_code=status.HTTP_303_SEE_OTHER)


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

    row = _build_row(parser, state.resources.ignore_hash_until)
    context = {"row": row, "today": date.today()}
    return templates.TemplateResponse(request, "admin/query_form.html", context)


@router.post("/{file_id}/ignore-hash")
def query_ignore_hash_set(
    file_id: str, state: ServerState = Depends(get_server_state), until: str = Form(...)
) -> RedirectResponse:
    if file_id not in state.query_catalog.by_file_id:
        return RedirectResponse(url="/admin/queries?error=Query+not+found", status_code=status.HTTP_303_SEE_OTHER)

    try:
        until_date = date.fromisoformat(until)
    except ValueError:
        return RedirectResponse(url="/admin/queries?error=Invalid+date", status_code=status.HTTP_303_SEE_OTHER)

    state.resources.ignore_hash_until[file_id] = datetime.combine(until_date, datetime.max.time())
    msg = f"Hash+check+for+{file_id}+ignored+until+{until_date.isoformat()}"
    return RedirectResponse(url=f"/admin/queries?msg={msg}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{file_id}/clear-hash-exemption")
def query_ignore_hash_clear(file_id: str, state: ServerState = Depends(get_server_state)) -> RedirectResponse:
    state.resources.ignore_hash_until.pop(file_id, None)
    return RedirectResponse(
        url=f"/admin/queries?msg=Hash+exemption+for+{file_id}+cleared", status_code=status.HTTP_303_SEE_OTHER
    )
