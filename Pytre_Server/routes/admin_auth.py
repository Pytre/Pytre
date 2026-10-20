import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote, quote_plus

import jwt
from fastapi import APIRouter, Depends, Form, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.base import RequestResponseEndpoint

from Pytre_Core.context import UserContext
from Pytre_Core.users import AuthType, User, hash_password, verify_password
from Pytre_Core.utils import log as _log

from Pytre_Server.auth_token import JWT_ALGORITHM, TokenType, create_token, decode_token
from Pytre_Server.oidc import OidcLoginError, handle_callback, start_login
from Pytre_Server.server_state import ServerResources, ServerState, get_server_state
from Pytre_Server.templating import templates


COOKIE_NAME = "admin_session"

router = APIRouter()


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


@dataclass
class LoginContext:
    next: str
    no_admin_yet: bool
    show_bootstrap_form: bool
    error: str = ""
    sso_providers: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, str | bool | list[dict[str, str]]]:
        return {
            "next": self.next,
            "no_admin_yet": self.no_admin_yet,
            "show_bootstrap_form": self.show_bootstrap_form,
            "error": self.error,
            "sso_providers": self.sso_providers,
        }


class AdminAuthRequired(Exception):
    """Raised by require_admin_session() when a request must be redirected to /admin/login."""

    def __init__(self, next_path: str) -> None:
        self.next_path = next_path


def admin_auth_required_handler(request: Request, exc: Exception) -> RedirectResponse:
    """Exception handler for AdminAuthRequired, raised by require_admin_session() whenever a protected
    /admin/* route is reached without a valid admin session. Redirects to the login page and deletes
    the cookie - a rejected cookie left in place would make GET /admin/login, which only checks its
    signature/exp, bounce straight back here in an endless redirect loop."""
    assert isinstance(exc, AdminAuthRequired)
    response = RedirectResponse(f"/admin/login?next={quote(exc.next_path)}", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(COOKIE_NAME, path="/admin")
    return response


def _is_loopback(request: Request) -> bool:
    """True only for a direct connection from the local machine.
    A request carrying any forwarding header went through a reverse proxy, so request.client.host
    can't prove the caller is local - never considered as a loopback then.
    Limit: a proxy that sets none of these headers can't be detected - it must be configured to send
    X-Forwarded-For/-Proto, and uvicorn must only be reachable through it (see --forwarded-allow-ips)."""

    forwarding_headers = ("x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "forwarded", "x-real-ip")
    if any(header in request.headers for header in forwarding_headers):
        return False

    return request.client is not None and request.client.host in ("127.0.0.1", "::1")


def _safe_next(next: str) -> str:
    """Only ever redirect to a path within /admin - guards against an open redirect via a
    crafted `next` value (this also incidentally rejects protocol-relative "//evil.com" URLs,
    which don't start with "/admin")."""
    return next if next.startswith("/admin") else "/admin"


def _next_path_for(request: Request) -> str:
    """The path to return to after re-login. Reusing request.url.path is only safe for a GET
    request - a POST-only action route (reload, delete...) would 405 if replayed as a GET
    after login."""
    return request.url.path if request.method == "GET" else "/admin"


def _set_admin_cookie(response: Response, request: Request, token: str, max_age: int) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=max_age,
        path="/admin",
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )


def _issue_session_cookie(response: RedirectResponse, request: Request, state: ServerState, username: str) -> None:
    token = create_token(username, state.app_ctx.settings.jwt_secret, TokenType.ADMIN)
    max_age = int(TokenType.ADMIN.value.total_seconds())
    _set_admin_cookie(response, request, token, max_age)


def touch_admin_session(token: str, key: str) -> tuple[str, int]:
    """Re-signs an already-validated admin session token with a fresh `last_activity` claim.
    `iat`/`exp`/`sub`/`type` are carried over unchanged. Returns the new token and the number of
    seconds remaining until its (unchanged) `exp`, so the cookie's max_age stays consistent with
    the real server-side expiration."""
    payload = decode_token(token, key, TokenType.ADMIN)
    payload["last_activity"] = datetime.now(timezone.utc).timestamp()
    new_token = jwt.encode(payload, key, algorithm=JWT_ALGORITHM)
    remaining = int(payload["exp"] - datetime.now(timezone.utc).timestamp())
    return new_token, remaining


def require_admin_session(request: Request, state: ServerState = Depends(get_server_state)) -> str:
    """FastAPI dependency, applied at the router level to every protected /admin/* route.
    Requires a valid admin_session cookie for a user that still exists, is still admin, hasn't had
    its password changed since the token was issued (same revocation check as the API's username_from_token),
    and hasn't been inactive for longer than TokenType.ADMIN.session_timeout."""

    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise AdminAuthRequired(_next_path_for(request))

    try:
        payload = decode_token(token, state.app_ctx.settings.jwt_secret, TokenType.ADMIN)
    except jwt.InvalidTokenError:
        raise AdminAuthRequired(_next_path_for(request))

    username = payload["sub"]
    issued_at = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)
    last_activity = datetime.fromtimestamp(payload.get("last_activity", payload["iat"]), tz=timezone.utc)
    inactivity_max = TokenType.ADMIN.session_timeout

    user = UserContext(state.app_ctx.users, username=username).user
    if (
        not user.exists
        or not user.admin
        or user.auth_type is AuthType.NONE
        or (user.password_changed_at is not None and issued_at < user.password_changed_at)
        or (inactivity_max is not None and (datetime.now(timezone.utc) - last_activity > inactivity_max))
    ):
        reason = ""
        if not user.exists:
            reason = "unknown user"
        elif user.auth_type is AuthType.NONE:
            reason = "disabled user"
        elif not user.admin:
            reason = "user is not an admin"

        if reason:
            log(f"Rejected admin session for '{username}': {reason}", logging.INFO)

        raise AdminAuthRequired(_next_path_for(request))

    request.state.admin_username = user.username
    return user.username


async def admin_session_middleware(request: Request, call_next: RequestResponseEndpoint) -> Response:
    """Registered at the app level (see app.py) - refreshes the admin_session cookie's `last_activity` claim
    on every request that went through require_admin_session successfully, so a session doesn't expire on the
    inactivity timeout. A no-op for every other request."""
    response = await call_next(request)

    admin_username = getattr(request.state, "admin_username", None)
    if admin_username is None:
        return response

    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return response

    state = get_server_state(request)
    new_token, remaining = touch_admin_session(token, state.app_ctx.settings.jwt_secret)
    _set_admin_cookie(response, request, new_token, max_age=remaining)

    return response


# ------------------------------------------------------------------------------------------
# Basic login / logout
# ------------------------------------------------------------------------------------------
@router.get("/login", response_class=HTMLResponse, response_model=None)
def admin_login_form(
    request: Request, state: ServerState = Depends(get_server_state), next: str = "", error: str = ""
) -> HTMLResponse | RedirectResponse:

    token = request.cookies.get(COOKIE_NAME)
    if token:
        try:
            decode_token(token, state.app_ctx.settings.jwt_secret, TokenType.ADMIN)
            return RedirectResponse(_safe_next(next), status_code=status.HTTP_303_SEE_OTHER)
        except jwt.InvalidTokenError:
            pass

    no_admin_yet = not state.app_ctx.users.users_admin_exists()

    sso_providers = [
        {"uuid": p.uuid, "label": p.label} for p in state.app_ctx.oidc_providers.get_usable_providers().values()
    ]

    context = LoginContext(
        next=next,
        no_admin_yet=no_admin_yet,
        show_bootstrap_form=no_admin_yet and _is_loopback(request),
        error=error,
        sso_providers=sso_providers,
    )

    return templates.TemplateResponse(request, "admin/login.html", context.to_dict())


@router.post("/login", response_class=HTMLResponse, response_model=None)
def admin_login_submit(
    request: Request,
    state: ServerState = Depends(get_server_state),
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
) -> HTMLResponse | RedirectResponse:
    resources: ServerResources = state.resources

    if resources.is_login_throttled(username):
        context = {"error": "Too many attempts, try again later", "next": next}
        return templates.TemplateResponse(
            request, "admin/login.html", context, status_code=status.HTTP_429_TOO_MANY_REQUESTS
        )

    user = UserContext(state.app_ctx.users, username=username).user
    is_valid_admin = user.exists and user.auth_type == AuthType.BASIC and user.admin
    password_ok = verify_password(password, user.password_hash if is_valid_admin else "")

    if not is_valid_admin or not password_ok:
        resources.throttle_record_failing(username)
        log(f"Failed admin login attempt for '{username}'", logging.WARNING)
        context = {"error": "Invalid username or password", "next": next}
        return templates.TemplateResponse(
            request, "admin/login.html", context, status_code=status.HTTP_401_UNAUTHORIZED
        )

    resources.throttle_reset_failings(username)
    log(f"Admin '{user.username}' logged in", logging.INFO)

    response = RedirectResponse(_safe_next(next), status_code=status.HTTP_303_SEE_OTHER)
    _issue_session_cookie(response, request, state, user.username)
    return response


@router.post("/bootstrap", response_class=HTMLResponse, response_model=None)
def admin_bootstrap_submit(
    request: Request,
    state: ServerState = Depends(get_server_state),
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    next: str = Form(""),
) -> HTMLResponse | RedirectResponse:
    """Creates the first admin account. Both conditions are re-checked here, never trusted from
    what the GET /login page happened to render - this is the only path that can ever create an
    admin account without already being logged in as one."""

    if not _is_loopback(request) or state.app_ctx.users.users_admin_exists():
        return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)

    context = LoginContext(next=next, no_admin_yet=True, show_bootstrap_form=True)

    if password != confirm_password:
        context.error = "Passwords do not match"
        return templates.TemplateResponse(
            request,
            "admin/login.html",
            context.to_dict(),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user = User(state.app_ctx.users, detect_user=False)
    user.username = username.strip()
    user.admin = True
    user.auth_type = AuthType.BASIC
    user.password_hash = hash_password(password)

    try:
        user.save()
    except ValueError as e:
        context.error = str(e)
        return templates.TemplateResponse(
            request,
            "admin/login.html",
            context.to_dict(),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    log(f"First admin account '{user.username}' created via bootstrap", logging.WARNING)

    response = RedirectResponse(_safe_next(next), status_code=status.HTTP_303_SEE_OTHER)
    _issue_session_cookie(response, request, state, user.username)
    return response


@router.post("/logout")
def admin_logout() -> RedirectResponse:
    response = RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(COOKIE_NAME, path="/admin")
    return response


# ------------------------------------------------------------------------------------------
# SSO login (OpenID Connect)
# ------------------------------------------------------------------------------------------
@router.get("/login/sso", response_model=None)
async def admin_login_sso_start(
    state: ServerState = Depends(get_server_state), provider: str = "", next: str = ""
) -> RedirectResponse:
    """Starts an SSO login for the OidcProvider identified by its uuid (never the issuer itself -
    keeps the URL opaque) and redirects the browser to the identity provider."""
    usable_by_uuid = {p.uuid: p for p in state.app_ctx.oidc_providers.get_usable_providers().values()}
    oidc_provider = usable_by_uuid.get(provider)
    if oidc_provider is None:
        return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)

    redirect_uri = f"{oidc_provider.public_base_url.rstrip('/')}/admin/login/sso/callback"

    try:
        auth_url = await start_login(state.resources, oidc_provider, redirect_uri, _safe_next(next))
    except OidcLoginError as e:
        return RedirectResponse(f"/admin/login?error={quote_plus(str(e))}", status_code=status.HTTP_303_SEE_OTHER)

    return RedirectResponse(auth_url, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login/sso/callback", response_model=None)
async def admin_login_sso_callback(
    request: Request,
    state: ServerState = Depends(get_server_state),
    state_token: str = Query("", alias="state"),
    code: str = "",
    error: str = "",
    error_description: str = "",
) -> RedirectResponse:
    """Completes an SSO login started above, then issues the admin session cookie exactly like
    POST /admin/login does for a password login. `state_token`/`code` come from the identity
    provider's redirect - `error`/`error_description` instead if the user denied consent or the
    provider itself failed."""
    if error:
        error_msg = quote_plus(error_description or error)
        return RedirectResponse(f"/admin/login?error={error_msg}", status_code=status.HTTP_303_SEE_OTHER)

    resources: ServerResources = state.resources

    # redirect_uri must exactly match the one start_login() used above - built from the same
    # provider, once it's known. When the state is unknown/expired, handle_callback() below rejects
    # it on its own with the precise reason; an empty redirect_uri is never used for a real exchange.
    pending = resources.oidc_peek_pending_login(state_token)
    oidc_provider = state.app_ctx.oidc_providers.get_all_providers().get(pending.provider_issuer) if pending else None
    redirect_uri = f"{oidc_provider.public_base_url.rstrip('/')}/admin/login/sso/callback" if oidc_provider else ""

    try:
        user, next_path = await handle_callback(
            resources, state.app_ctx.oidc_providers, state.app_ctx.users, state_token, code, redirect_uri
        )
    except OidcLoginError as e:
        return RedirectResponse(f"/admin/login?error={quote_plus(str(e))}", status_code=status.HTTP_303_SEE_OTHER)

    if not user.admin:
        error_msg = quote_plus("This account is not an administrator.")
        return RedirectResponse(f"/admin/login?error={error_msg}", status_code=status.HTTP_303_SEE_OTHER)

    response = RedirectResponse(_safe_next(next_path), status_code=status.HTTP_303_SEE_OTHER)
    _issue_session_cookie(response, request, state, user.username)
    return response
