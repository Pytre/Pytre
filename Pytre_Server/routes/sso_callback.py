"""
The server's single OIDC redirect URI (CALLBACK_PATH), outside /admin so the client's SSO keeps working when
a proxy filters /admin. Each login is finished the way its flow requires: an admin session cookie for the web
administration, a one-time code bridged to the loopback listener for the client.
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from Pytre_Server.oidc import CALLBACK_PATH
from Pytre_Server.routes import admin_auth, client_sso
from Pytre_Server.server_state import ServerState, SsoFlow, get_server_state


router = APIRouter()


@router.get(CALLBACK_PATH, response_model=None)
async def sso_callback(
    request: Request,
    state: ServerState = Depends(get_server_state),
    state_token: str = Query("", alias="state"),
    code: str = "",
    error: str = "",
    error_description: str = "",
) -> RedirectResponse | HTMLResponse:
    """Hit by the identity provider at the end of every SSO login, `state_token` telling which flow started it.
    Unknown or expired, neither the flow nor a page to send the browser back to is known: a generic page is shown."""
    pending = state.resources.oidc_peek_pending_login(state_token)
    if pending is None:
        return HTMLResponse(client_sso.LOGIN_FAILED_HTML)

    if pending.flow is SsoFlow.ADMIN:
        return await admin_auth.finish_sso_login(state, state_token, code, error, error_description, request=request)

    return await client_sso.finish_sso_login(
        state, state_token, code, error, error_description, next_path=pending.next_path
    )
