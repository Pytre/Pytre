from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import jwt
from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from Pytre_Core.context import UserContext
from Pytre_Core.users import AuthType, User, hash_password, verify_password
from Pytre_Server.auth_token import TokenType, create_token, decode_token
from Pytre_Server.server_state import ServerResources, ServerState, get_server_state

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

COOKIE_NAME = "admin_session"

router = APIRouter()


@dataclass
class LoginContext:
    next: str
    no_admin_yet: bool
    show_bootstrap_form: bool
    error: str = ""

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "next": self.next,
            "no_admin_yet": self.no_admin_yet,
            "show_bootstrap_form": self.show_bootstrap_form,
            "error": self.error,
        }


class AdminAuthRequired(Exception):
    """Raised by require_admin_session() when a request must be redirected to /admin/login."""

    def __init__(self, next_path: str) -> None:
        self.next_path = next_path


def admin_auth_required_handler(request: Request, exc: Exception) -> RedirectResponse:
    assert isinstance(exc, AdminAuthRequired)
    return RedirectResponse(f"/admin/login?next={quote(exc.next_path)}", status_code=status.HTTP_303_SEE_OTHER)


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


def _issue_session_cookie(response: RedirectResponse, request: Request, state: ServerState, username: str) -> None:
    token = create_token(username, state.app_ctx.settings.jwt_secret, TokenType.ADMIN)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(TokenType.ADMIN.value.total_seconds()),
        path="/admin",
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )


def require_admin_session(request: Request, state: ServerState = Depends(get_server_state)) -> str:
    """FastAPI dependency, applied at the router level to every protected /admin/* route.
    Requires a valid admin_session cookie for a user that still exists, is still admin, and hasn't had
    its password changed since the token was issued (same revocation check as the API's username_from_token)"""

    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise AdminAuthRequired(_next_path_for(request))

    try:
        payload = decode_token(token, state.app_ctx.settings.jwt_secret, TokenType.ADMIN)
    except jwt.InvalidTokenError:
        raise AdminAuthRequired(_next_path_for(request))

    username = payload["sub"]
    issued_at = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)

    user = UserContext(state.app_ctx.users, username=username).current_user
    if not user.exists or not user.admin:
        raise AdminAuthRequired(_next_path_for(request))
    if user.password_changed_at is not None and issued_at < user.password_changed_at:
        raise AdminAuthRequired(_next_path_for(request))

    request.state.admin_username = user.username
    return user.username


# ------------------------------------------------------------------------------------------
# Login / logout
# ------------------------------------------------------------------------------------------
@router.get("/login", response_class=HTMLResponse, response_model=None)
def admin_login_form(
    request: Request, state: ServerState = Depends(get_server_state), next: str = ""
) -> HTMLResponse | RedirectResponse:

    token = request.cookies.get(COOKIE_NAME)
    if token:
        try:
            decode_token(token, state.app_ctx.settings.jwt_secret, TokenType.ADMIN)
            return RedirectResponse(_safe_next(next), status_code=status.HTTP_303_SEE_OTHER)
        except jwt.InvalidTokenError:
            pass

    no_admin_yet = not state.app_ctx.users.users_admin_exists()

    context = LoginContext(
        next=next,
        no_admin_yet=no_admin_yet,
        show_bootstrap_form=no_admin_yet and _is_loopback(request),
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

    user = UserContext(state.app_ctx.users, username=username).current_user
    is_valid_admin = user.exists and user.auth_type == AuthType.BASIC and user.admin
    password_ok = verify_password(password, user.password_hash if is_valid_admin else "")

    if not is_valid_admin or not password_ok:
        resources.throttle_record_failing(username)
        context = {"error": "Invalid username or password", "next": next}
        return templates.TemplateResponse(
            request, "admin/login.html", context, status_code=status.HTTP_401_UNAUTHORIZED
        )

    resources.throttle_reset_failings(username)

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

    response = RedirectResponse(_safe_next(next), status_code=status.HTTP_303_SEE_OTHER)
    _issue_session_cookie(response, request, state, user.username)
    return response


@router.post("/logout")
def admin_logout() -> RedirectResponse:
    response = RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(COOKIE_NAME, path="/admin")
    return response
