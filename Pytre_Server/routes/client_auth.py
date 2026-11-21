"""Authentication of the client: sign-in by password, renewal of the tokens, change of password and the sign-in
methods offered. Also the verification of the access token, a dependency of every other client route."""

import asyncio
import logging
from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel

from Pytre_Core.context import UserContext
from Pytre_Core.users import AuthType, hash_password
from Pytre_Core.utils import log as _log

from Pytre_Server.audit import audit, shorten, sign_in_failure
from Pytre_Server.auth_token import create_token, TokenType, decode_token
from Pytre_Server.oidc import OidcReverifyError, verify_still_active
from Pytre_Server.server_state import (
    PasswordChecksSaturated,
    ServerResources,
    ServerState,
    client_host,
    get_server_state,
)


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


router = APIRouter()


class AuthMethodsResponse(BaseModel):
    sso_enabled: bool


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    refresh_token: str
    access_token: str


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None


class ChangePasswordRequest(BaseModel):
    new_password: str


class ChangePasswordResponse(BaseModel):
    refresh_token: str
    access_token: str


def username_from_token(token: str, token_type: TokenType, state: ServerState) -> str:
    """Verifies if the token is valid and return its username.
    401 on any failure - missing/malformed header, invalid/expired token, or a token
    issued before the user's password was last changed (see User.password_hash setter)."""

    try:
        payload = decode_token(token, state.app_ctx.settings.jwt_secret, token_type)
    except jwt.InvalidTokenError as e:
        http_error = status.HTTP_401_UNAUTHORIZED
        raise HTTPException(
            status_code=http_error, detail=f"Invalid or expired {token_type.name.lower()} token"
        ) from e

    username = payload["sub"]
    issued_at = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)

    user = UserContext(state.app_ctx.users, username=username).user

    if (
        not user.exists
        or user.auth_type is AuthType.NONE
        or (user.password_changed_at is not None and issued_at < user.password_changed_at)
    ):
        if not user.exists or user.auth_type is AuthType.NONE:
            reason = "unknown user" if not user.exists else "disabled user"
            log(f"Rejected {token_type.name.lower()} token for '{username}': {reason}", logging.INFO)
        http_error = status.HTTP_401_UNAUTHORIZED
        raise HTTPException(status_code=http_error, detail=f"Invalid or expired {token_type.name.lower()} token")

    return username


def require_access_token(
    state: ServerState = Depends(get_server_state), authorization: str | None = Header(None)
) -> str:
    """FastAPI dependency: verifies the Bearer token and returns its username.
    401 on any failure - missing/malformed header, invalid/expired token, or a token
    issued before the user's password was last changed (see User.password_hash setter)."""
    if not authorization or not authorization.startswith("Bearer "):
        http_error = status.HTTP_401_UNAUTHORIZED
        raise HTTPException(status_code=http_error, detail="Missing or invalid Authorization header")

    token = authorization.removeprefix("Bearer ")
    return username_from_token(token, TokenType.ACCESS, state)


@router.get("/auth-methods")
async def auth_methods(state: ServerState = Depends(get_server_state)) -> AuthMethodsResponse:
    """Lets the client know whether a single usable OIDC provider is configured, so its remote
    config UI can offer SSO as a login method - no provider identification needed, the client
    never picks one (see /login/sso/start)."""
    usable = state.app_ctx.oidc_providers.get_usable_providers()
    return AuthMethodsResponse(sso_enabled=len(usable) == 1)


@router.post("/login")
async def login(
    request: LoginRequest, http_request: Request, state: ServerState = Depends(get_server_state)
) -> LoginResponse:
    """Authenticate with a username/password, return a token usable as a Bearer token on refresh route.
    Always the same generic error on failure - don't reveal whether the username exists,
    to avoid account enumeration (by response content or timing).
    Failed logins are throttled per username and per client address, the latter shared with /admin/login.
    The password check runs in a worker thread so the event loop stays free, and is capped server-wide: 429 when no
    check slot is freed in time."""
    resources: ServerResources = state.resources
    host = client_host(http_request)

    if resources.login_throttle.is_throttled(request.username, host):
        http_error = status.HTTP_429_TOO_MANY_REQUESTS
        raise HTTPException(status_code=http_error, detail="Too many attempts, try again later")

    user = UserContext(state.app_ctx.users, username=request.username).user
    is_basic_existing_user = user.exists and user.auth_type == AuthType.BASIC
    password_hash = user.password_hash if is_basic_existing_user else ""

    try:
        password_ok = await resources.verify_password_capped(request.password, password_hash)
    except PasswordChecksSaturated:
        http_error = status.HTTP_429_TOO_MANY_REQUESTS
        raise HTTPException(status_code=http_error, detail="Too many attempts, try again later")

    if not is_basic_existing_user or not password_ok:
        resources.login_throttle.record_failure(request.username, host)

        attempted = shorten(request.username)
        reason = sign_in_failure(user)
        audit("client.login.failed", host, f"Failed login attempt for {attempted!r}: {reason}", target=attempted)

        http_error = status.HTTP_401_UNAUTHORIZED
        raise HTTPException(status_code=http_error, detail="Invalid username or password")

    resources.login_throttle.record_success(request.username, host)
    audit("client.login", host, f"User {user.username!r} logged in", actor=user.username, target=user.username)
    return LoginResponse(
        refresh_token=create_token(user.username, state.app_ctx.settings.jwt_secret, TokenType.REFRESH),
        access_token=create_token(user.username, state.app_ctx.settings.jwt_secret, TokenType.ACCESS),
    )


@router.post("/refresh", response_model_exclude_none=True)
async def refresh(
    request: RefreshRequest, http_request: Request, state: ServerState = Depends(get_server_state)
) -> RefreshResponse:
    """Authenticate with a refresh token and return a token usable as a Bearer token on other routes.
    For an OIDC user, this also periodically re-confirms with the IdP that the account is still active
    (throttled) before minting a new access token  - and, when that check just ran and succeeded, rolls
    the refresh token itself forward too. A continuously-active OIDC account never has to go through an
    interactive browser login again.
    A valid refresh token keeps the client address known to the account, see LoginThrottle.remember_address()."""

    username = username_from_token(request.refresh_token, TokenType.REFRESH, state)

    user = UserContext(state.app_ctx.users, username=username).user
    verified = False
    if user.auth_type is AuthType.OIDC:
        try:
            verified = await verify_still_active(state.resources, state.app_ctx.oidc_providers, user)
        except OidcReverifyError as e:
            log(f"Rejected refresh token for '{username}': {e}", logging.INFO)
            http_error = status.HTTP_401_UNAUTHORIZED
            raise HTTPException(status_code=http_error, detail="Invalid or expired refresh token") from e

    refresh_token = None
    if verified:
        refresh_token = create_token(username, state.app_ctx.settings.jwt_secret, TokenType.REFRESH)

    state.resources.login_throttle.remember_address(username, client_host(http_request))

    return RefreshResponse(
        access_token=create_token(username, state.app_ctx.settings.jwt_secret, TokenType.ACCESS),
        refresh_token=refresh_token,
    )


@router.post("/change-password")
async def change_password(
    request: ChangePasswordRequest,
    http_request: Request,
    username: str = Depends(require_access_token),
    state: ServerState = Depends(get_server_state),
) -> ChangePasswordResponse:
    """Self-service password change. Returns a fresh token immediately: the one used
    to authenticate this call becomes invalid after new password_hash is set."""

    user = UserContext(state.app_ctx.users, username=username).user
    user.password_hash = await asyncio.to_thread(hash_password, request.new_password)
    await asyncio.to_thread(user.save)

    audit(
        "client.password_change",
        client_host(http_request),
        f"User {username!r} changed their password",
        actor=username,
        target=username,
    )

    return ChangePasswordResponse(
        refresh_token=create_token(username, state.app_ctx.settings.jwt_secret, TokenType.REFRESH),
        access_token=create_token(username, state.app_ctx.settings.jwt_secret, TokenType.ACCESS),
    )
