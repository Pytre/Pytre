"""What the client can use: the SQL servers the user may query, and the queries the user may run on one of them."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query as QueryParam
from pydantic import BaseModel, Json

from Pytre_Core.context import UserContext
from Pytre_Core.servers import filter_servers
from Pytre_Core.user_prefs import DisplayFormat
from Pytre_Core.sql_query.loader import build_queries_from_parsers, filter_queries

from Pytre_Server.server_state import ServerState, get_server_state
from Pytre_Server.routes.client_auth import require_access_token


router = APIRouter()


class QueriesRequest(BaseModel):
    """Request parameters for GET /queries"""

    server_id: str
    display_format: Json[DisplayFormat]  # sent as JSON, validated by DisplayFormat.__post_init__


class ServersResponse(BaseModel):
    """user_infos carries what a client needs to display queries correctly using remote's own user data."""

    servers: list[dict]
    user_infos: dict


@router.get("/servers")
async def list_servers(
    username: str = Depends(require_access_token), state: ServerState = Depends(get_server_state)
) -> ServersResponse:
    """Return the SQL servers the client can use, as id/description only, plus the user's own infos.
    The client never connects directly, the server does."""
    user_ctx = UserContext(state.app_ctx.users, username=username)
    user = user_ctx.user
    grp_authorized = user.groups if not user.admin else None
    servers_dict = filter_servers(state.app_ctx.servers.servers_dict, grp_filter=grp_authorized)

    return ServersResponse(
        servers=[{"id": s.id, "description": s.description} for s in list(servers_dict.values())],
        user_infos=user.export_infos_for_remote(),
    )


@router.get("/queries")
async def list_queries(
    request: Annotated[QueriesRequest, QueryParam()],
    username: str = Depends(require_access_token),
    state: ServerState = Depends(get_server_state),
) -> list[dict]:
    """Return the queries the client can use."""
    user_ctx = UserContext(state.app_ctx.users, username=username, display_format_override=request.display_format)
    queries, _errors = build_queries_from_parsers(state.app_ctx, user_ctx, state.query_catalog.parsers)
    filtered = filter_queries(state.app_ctx.servers, queries, request.server_id, user_ctx.user)
    return [q.serialize() for q in filtered]
