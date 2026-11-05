"""
Admin webui route for the "Query Logs" page - executions logged by this server (ServerDriven's database), either raw
or through its stats views, read-only.
Gated by require_admin_session (see admin_router.py)
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from Pytre_Server.query_logs_reader import QueryLogsSource, QueryLogsUnavailable, read_source, start_to_timestamp
from Pytre_Server.server_state import ServerState, get_server_state
from Pytre_Server.templating import templates


router = APIRouter(prefix="/query-logs")

PAGE_SIZE = 200
TIMESTAMP_COLS = ("START", "LAST_RUN")  # shown in the browser's own time zone


@router.get("", response_class=HTMLResponse)
def query_logs_page(
    request: Request,
    state: ServerState = Depends(get_server_state),
    source: QueryLogsSource = QueryLogsSource.QUERIES_EXEC,
    page: int = Query(1, ge=1),
) -> HTMLResponse:
    context = {
        "sources": list(QueryLogsSource),
        "source": source,
        "page": page,
        "columns": [],
        "rows": [],
        "timestamp_indexes": [],
        "has_next": False,
        "error": None,
    }

    execution_logs = state.resources.execution_logs
    if execution_logs is None:
        context["error"] = "Query logs are disabled, they can be enabled in the general settings"
        return templates.TemplateResponse(request, "admin/query_logs.html", context)

    try:
        # one row more than displayed tells whether there is a next page
        offset = (page - 1) * PAGE_SIZE
        columns, rows = read_source(execution_logs.db_uri, source, limit=PAGE_SIZE + 1, offset=offset)
    except QueryLogsUnavailable as e:
        context["error"] = f"Query logs unavailable: {e}"
        return templates.TemplateResponse(request, "admin/query_logs.html", context)

    timestamp_indexes = [i for i, column in enumerate(columns) if column in TIMESTAMP_COLS]
    context["columns"] = columns
    context["rows"] = [
        [start_to_timestamp(value) if i in timestamp_indexes else value for i, value in enumerate(row)]
        for row in rows[:PAGE_SIZE]
    ]
    context["timestamp_indexes"] = timestamp_indexes
    context["has_next"] = len(rows) > PAGE_SIZE

    return templates.TemplateResponse(request, "admin/query_logs.html", context)
