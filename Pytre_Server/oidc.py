"""
Server-side OpenID Connect relying party flow - authorization code + PKCE via Authlib, ID token
validation via OidcClient (Pytre_Core/oidc_client.py), and linking the result to a user.
Routes translate what this module raises/returns into HTTP responses, so the flow itself is
testable without going through a route at all.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx2
from authlib.common.errors import AuthlibBaseError
from authlib.integrations.httpx_client import AsyncOAuth2Client

from Pytre_Core.oidc_client import OidcError, new_token
from Pytre_Core.oidc_providers import OidcProvider, OidcProviders
from Pytre_Core.users import AuthType, OidcLink, User, Users
from Pytre_Core.utils import log as _log

from Pytre_Server.server_state import ServerResources


REVERIFY_INTERVAL = timedelta(hours=1)  # how often an OIDC account is re-checked against its IdP
REVERIFY_GRACE_PERIOD = timedelta(hours=24)  # how long an unreachable IdP is tolerated before rejecting


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


class OidcLoginError(Exception):
    """Raised for any failure of the SSO login flow - an unknown/expired/reused state, a failed
    code exchange, an invalid ID token, or a rejected user linkage. The route only ever needs to
    catch this one type; the underlying cause (OidcError, Authlib's own errors) is kept as
    __cause__ when there is one."""


class OidcReverifyError(Exception):
    """Raised by verify_still_active() to reject a /refresh call for an OIDC user: the account's
    identity-provider link could not be (re)confirmed active - the IdP explicitly revoked it, no
    refresh token/usable provider exists to even ask, or an IdP/network outage lasted longer than
    the tolerated grace period. Distinct from OidcLoginError (interactive SSO login failures) -
    this fires from the already-authenticated /refresh path, not during the login handshake."""


async def start_login(resources: ServerResources, provider: OidcProvider, redirect_uri: str, next_path: str) -> str:
    """Registers a pending login and returns the URL to send the browser to."""
    try:
        discovery = await resources.oidc_client.discover(provider.issuer)
    except OidcError as e:
        raise OidcLoginError(f"Could not reach the identity provider: {e}") from e

    nonce = new_token()
    code_verifier = new_token()
    state_token = resources.oidc_start_pending_login(provider.issuer, nonce, code_verifier, next_path)

    # add offline_access to scope if missing from scope
    scope = " ".join(s for s in provider.scopes.split() if s != "offline_access") + " offline_access"

    client = AsyncOAuth2Client(
        provider.client_id,
        provider.client_secret,
        scope=scope,
        redirect_uri=redirect_uri,
        code_challenge_method="S256",
    )
    auth_url, _ = client.create_authorization_url(
        discovery.authorization_endpoint, state=state_token, code_verifier=code_verifier, nonce=nonce
    )

    return auth_url


async def handle_callback(
    resources: ServerResources,
    oidc_providers: OidcProviders,
    users: Users,
    state_token: str,
    code: str,
    redirect_uri: str,
) -> tuple[User, str]:
    """Completes a pending login: exchanges `code`, validates the ID token, links it to a
    user. Returns (user, next_path) on success. The pending entry is consumed (single-use)
    whether this succeeds or not, since it's popped before any of the steps that can fail."""
    pending = resources.oidc_pop_pending_login(state_token)
    if pending is None:
        raise OidcLoginError("This sign-in link has expired or was already used - please try again.")

    # retrieve oidc provider - re-looked-up now, not trusted from what start_login saw earlier
    provider = oidc_providers.get_all_providers().get(pending.provider_issuer)
    if provider is None or not provider.is_usable:
        raise OidcLoginError("This identity provider is no longer usable.")

    # re-fetch discovery (cached) to get the token endpoint
    try:
        discovery = await resources.oidc_client.discover(provider.issuer)
    except OidcError as e:
        raise OidcLoginError(f"Could not reach the identity provider: {e}") from e

    # fetch OAuth2 token
    client = AsyncOAuth2Client(provider.client_id, provider.client_secret, redirect_uri=redirect_uri)
    try:
        token: dict = await client.fetch_token(
            discovery.token_endpoint, code=code, code_verifier=pending.code_verifier
        )
    except (AuthlibBaseError, httpx2.HTTPStatusError, httpx2.RequestError) as e:
        raise OidcLoginError(f"Could not exchange the authorization code: {e}") from e
    finally:
        await client.aclose()  # pyright: ignore[reportAttributeAccessIssue]

    # get id_token (not every IdP returns one) and refresh_token
    id_token = token.get("id_token")
    if not id_token:
        raise OidcLoginError("The identity provider didn't return an ID token.")

    refresh_token = token.get("refresh_token", "")

    # validate claims - signature, iss, aud, exp/iat, and nonce against the one stored at start_login
    try:
        claims = await resources.oidc_client.validate_id_token(
            id_token, discovery, client_id=provider.client_id, nonce=pending.nonce
        )
    except OidcError as e:
        raise OidcLoginError(f"Invalid ID token: {e}") from e

    # link user - does blocking KeePass I/O, deferred to a worker thread so the event loop is not stalled
    user = await asyncio.to_thread(link_user, users, provider, claims, refresh_token)

    return user, pending.next_path


def link_user(users: Users, provider: OidcProvider, claims: dict, refresh_token: str = "") -> User:
    """Finds the Pytre user for `claims`, linking it to the IdP account (`sub`) on first login.
    Never creates a user - the account must already exist, with AuthType.OIDC. Once linked, only
    `sub` is trusted for subsequent logins, even if the username claim's value changes later at the IdP."""
    claimed_username = claims.get(provider.username_claim)
    if not claimed_username:
        raise OidcLoginError(f"The identity provider didn't send a '{provider.username_claim}' claim.")

    sub = claims["sub"]  # already required present by OidcClient.validate_id_token()

    # try to get user with a matching sub otherwise instanciate User with claimed_username
    user = users.find_user_by_oidc_link(provider.issuer, sub)
    if user is None:
        user = User(users, username=str(claimed_username))

    # validate that user exists and is set to use OIDC as authentification method
    if not user.exists or user.auth_type is not AuthType.OIDC:
        log(f"SSO rejected: '{claimed_username}' is not an OIDC-enabled account", logging.INFO)
        raise OidcLoginError(f"No OIDC-enabled account matches '{claimed_username}'.")

    link = user.oidc_link
    if link is None or link.sub == "" or (link.issuer == provider.issuer and link.sub == sub):
        not_already_linked = link is None or link.sub == ""
        user.oidc_link = OidcLink(
            issuer=provider.issuer, sub=sub, refresh_token=refresh_token, last_verified=datetime.now(timezone.utc)
        )
        user.save()
        if not_already_linked:
            log(f"SSO: linked Oidc account '{user.username}' to its identity provider account", logging.INFO)
    else:
        log(f"SSO rejected: '{user.username}' is linked to a different identity provider account", logging.INFO)
        raise OidcLoginError(f"'{claimed_username}' is linked to a different identity provider account.")

    return user


async def verify_still_active(resources: ServerResources, oidc_providers: OidcProviders, user: User) -> bool:
    """For an OIDC user, periodically re-confirms with the identity provider that the account is still active.
    Throttled by user.oidc_link.last_verified (REVERIFY_INTERVAL) so most /refresh calls do nothing.

    Raises OidcReverifyError to reject the /refresh call outright:
      - no OidcLink, or no refresh_token available
      - the linked provider no longer exists, or isn't currently usable
      - the IdP explicitly rejected the refresh grant (invalid_grant - revoked/disabled at the IdP)
      - an IdP/network failure has persisted beyond REVERIFY_GRACE_PERIOD since the last success

    Returns True only when a fresh, successful check against the IdP was performed - the caller uses this
    to decide whether to roll the caller-facing refresh token forward.
    Returns False (no-op) when re-verification isn't due yet, or when it fails but is still within the tolerated
    grace window. On success, user.oidc_link is replaced (refresh_token rotated if the IdP sent a new one,
    last_verified bumped to now) and persisted via user.save() - deferred to a worker thread since save() does
    blocking KeePass I/O. A tolerated failure does NOT touch oidc_link - only an actual successful check may reset
    the grace clock."""

    link = user.oidc_link
    now = datetime.now(timezone.utc)

    # confirm a refresh token exists
    if link is None or not link.refresh_token:
        log(f"OIDC re-verification rejected for '{user.username}': no refresh token available", logging.INFO)
        raise OidcReverifyError(f"'{user.username}' has no identity-provider refresh token available.")

    # return directly if checked recently
    if link.last_verified is not None and (now - link.last_verified) <= REVERIFY_INTERVAL:
        return False

    # raise an error if the IdP has been disabled
    provider = oidc_providers.get_all_providers().get(link.issuer)
    if provider is None or not provider.is_usable:
        msg = f"OIDC re-verification rejected for '{user.username}': provider '{link.issuer}' missing/disabled"
        log(msg, logging.INFO)
        raise OidcReverifyError(f"The IdP '{link.issuer}' for '{user.username}' is no longer usable.")

    # re-fetch discovery (cached) to get the token endpoint
    try:
        discovery = await resources.oidc_client.discover(provider.issuer)
    except OidcError as e:
        _tolerate_or_reject(user, link, now, e)
        return False

    # re-fetch token from the IdP
    client = AsyncOAuth2Client(provider.client_id, provider.client_secret)
    try:
        token: dict = await client.refresh_token(discovery.token_endpoint, refresh_token=link.refresh_token)
    except AuthlibBaseError as e:
        if getattr(e, "error", None) == "invalid_grant":
            log(f"OIDC re-verification rejected for '{user.username}': IdP revoked the refresh token", logging.INFO)
            raise OidcReverifyError(f"'{user.username}' is no longer active at its identity provider.") from e
        _tolerate_or_reject(user, link, now, e)
        return False
    except (httpx2.HTTPStatusError, httpx2.RequestError) as e:
        _tolerate_or_reject(user, link, now, e)
        return False
    finally:
        await client.aclose()  # pyright: ignore[reportAttributeAccessIssue]

    # update oidc link (update refresh_token or fall back to the old one) and set last_verified
    user.oidc_link = OidcLink(
        issuer=link.issuer,
        sub=link.sub,
        refresh_token=token.get("refresh_token", link.refresh_token),
        last_verified=now,
    )

    await asyncio.to_thread(user.save)

    return True


def _tolerate_or_reject(user: User, link: OidcLink, now: datetime, cause: BaseException) -> None:
    """Shared outcome for 'couldn't even ask the IdP' (discovery failure, network error, 5xx):
    tolerate it if still within REVERIFY_GRACE_PERIOD of the last successful check, otherwise
    raise OidcReverifyError. A link that was never successfully verified (last_verified is None)
    gets no grace - there is no earlier success to extend."""
    if link.last_verified is not None and (now - link.last_verified) <= REVERIFY_GRACE_PERIOD:
        msg = f"OIDC re-verification for '{user.username}' tolerated a provider/network failure: {cause}"
        log(msg, logging.WARNING)
        return

    msg = f"OIDC re-verification rejected for '{user.username}': provider unreachable beyond grace period: {cause}"
    log(msg, logging.WARNING)
    err_msg = f"Could not reach the identity provider to confirm '{user.username}' is still active."
    raise OidcReverifyError(err_msg) from cause
