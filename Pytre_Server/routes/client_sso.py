"""
Server-side routes for mediated-mode SSO login: the desktop client opens the system browser here,
the browser completes a normal OIDC login via the existing relying-party flow (Pytre_Server/oidc.py,
shared with the admin webui), and is redirected back to a loopback listener the client itself started -
never to an admin-configured URL, since the target varies per client instance/port.

Only one usable OIDC provider is supported for this flow - the client has no way to pick one
(see sso_start()'s own check, and GET /auth-methods in client.py).
"""

import json
from urllib.parse import quote_plus

from authlib.oauth2.rfc7636 import create_s256_code_challenge
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from Pytre_Server.auth_token import TokenType, create_token
from Pytre_Server.oidc import OidcLoginError, handle_callback, start_login
from Pytre_Server.server_state import ServerState, get_server_state


router = APIRouter(prefix="/login/sso")

_CALLBACK_SUFFIX = "/login/sso/callback"
_LOGIN_FAILED_HTML = "<html><body>Login failed or expired - you may close this window and try again.</body></html>"


def _build_loopback_redirect_uri(port: int) -> str:
    """The only client-controlled input to this URL is `port` (validated as 1-65535 by FastAPI's
    Query(...) before this ever runs) - scheme, host and path are always literals, so there's no
    URL to parse/validate: a forged port can at most redirect to a different port on 127.0.0.1,
    never off this machine."""
    return f"http://127.0.0.1:{port}/"


def _encode_bridge(redirect_uri: str, client_state: str, code_challenge: str) -> str:
    return json.dumps({"redirect_uri": redirect_uri, "state": client_state, "code_challenge": code_challenge})


def _decode_bridge(next_path: str) -> dict[str, str] | None:
    """Defensive parse of what _encode_bridge() produced - None on anything malformed, since a
    pending entry's next_path is opaque storage from oidc.py's point of view, never validated there."""
    try:
        bridge = json.loads(next_path)
    except ValueError:
        return None

    if not isinstance(bridge, dict):
        return None
    if not all(isinstance(bridge.get(key), str) for key in ("redirect_uri", "state", "code_challenge")):
        return None

    return bridge


def _error_redirect(redirect_uri: str, client_state: str, message: str) -> RedirectResponse:
    url = f"{redirect_uri}?error={quote_plus(message)}&state={quote_plus(client_state)}"
    return RedirectResponse(url, status_code=303)


class SsoTokenRequest(BaseModel):
    code: str
    code_verifier: str


class SsoTokenResponse(BaseModel):
    refresh_token: str
    access_token: str


@router.get("/start", response_model=None)
async def sso_start(
    state: ServerState = Depends(get_server_state),
    port: int = Query(..., ge=1, le=65535),
    client_state: str = Query(..., alias="state", min_length=16, max_length=256),
    code_challenge: str = Query(..., min_length=43, max_length=128),
) -> RedirectResponse:
    """The client opens its browser here after starting its own loopback listener on `port`."""
    redirect_uri = _build_loopback_redirect_uri(port)

    usable = list(state.app_ctx.oidc_providers.get_usable_providers().values())
    if len(usable) != 1:
        return _error_redirect(redirect_uri, client_state, "SSO is not available on this server.")
    provider = usable[0]

    bridge = _encode_bridge(redirect_uri, client_state, code_challenge)
    server_redirect_uri = f"{provider.public_base_url.rstrip('/')}{_CALLBACK_SUFFIX}"

    try:
        auth_url = await start_login(state.resources, provider, server_redirect_uri, bridge)
    except OidcLoginError as e:
        return _error_redirect(redirect_uri, client_state, str(e))

    return RedirectResponse(auth_url, status_code=303)


@router.get("/callback", response_model=None)
async def sso_callback(
    state: ServerState = Depends(get_server_state),
    state_token: str = Query("", alias="state"),
    code: str = "",
    error: str = "",
    error_description: str = "",
) -> RedirectResponse | HTMLResponse:
    """Hit by the IdP, exactly like the admin webui's own callback - completes the login, then
    bridges back to the client's loopback listener with a short-lived, single-use code (never
    the IdP's own tokens) for it to exchange via POST /login/sso/token."""
    resources = state.resources

    pending = resources.oidc_peek_pending_login(state_token)
    bridge = _decode_bridge(pending.next_path) if pending else None
    if bridge is None:
        # state unknown/expired/forged - no redirect_uri to safely bounce back to
        return HTMLResponse(_LOGIN_FAILED_HTML)

    if error:
        return _error_redirect(bridge["redirect_uri"], bridge["state"], error_description or error)

    # redirect_uri must exactly match the one sso_start() used above - rebuilt from the same
    # provider, re-looked-up by issuer, never trusted from a later request parameter
    all_providers = state.app_ctx.oidc_providers.get_all_providers()
    provider = all_providers.get(pending.provider_issuer) if pending else None
    server_redirect_uri = f"{provider.public_base_url.rstrip('/')}{_CALLBACK_SUFFIX}" if provider else ""

    try:
        user, _next_path = await handle_callback(
            resources, state.app_ctx.oidc_providers, state.app_ctx.users, state_token, code, server_redirect_uri
        )
    except OidcLoginError as e:
        return _error_redirect(bridge["redirect_uri"], bridge["state"], str(e))

    # unlike the admin webui, no admin-only check here - any AuthType.OIDC user link_user() accepts can sign in
    client_code = resources.client_code_start_pending(user.username, bridge["code_challenge"])
    url = f"{bridge['redirect_uri']}?code={quote_plus(client_code)}&state={quote_plus(bridge['state'])}"
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/token")
async def sso_token(request: SsoTokenRequest, state: ServerState = Depends(get_server_state)) -> SsoTokenResponse:
    """The client's last step: exchanges the one-time code from the loopback redirect, plus its
    own PKCE verifier, for a normal server token pair - from here on, /refresh works unchanged."""
    pending = state.resources.client_code_pop_pending(request.code)
    if pending is None or create_s256_code_challenge(request.code_verifier) != pending.code_challenge:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired code")

    jwt_secret = state.app_ctx.settings.jwt_secret
    return SsoTokenResponse(
        refresh_token=create_token(pending.username, jwt_secret, TokenType.REFRESH),
        access_token=create_token(pending.username, jwt_secret, TokenType.ACCESS),
    )
