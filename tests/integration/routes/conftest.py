"""
Shared fixtures/helpers for the route integration tests.

Most of them fake an OIDC identity provider - used by both the admin webui's own SSO login
(test_admin_auth.py) and the desktop client's mediated-mode SSO login (test_client_sso.py). Both fake
the IdP the same way (discovery/JWKS via a MockTransport, code exchange via a fake AsyncOAuth2Client) -
the protocol pieces already have their own dedicated coverage (tests/unit/core/test_oidc_client.py,
tests/unit/server/test_oidc.py).
"""

import asyncio
import json
import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import cast
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI
import httpx2
import jwt
import pytest
from authlib.integrations.httpx_client import AsyncOAuth2Client
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from Pytre_Core.oidc_providers import OidcProvider
from Pytre_Core.users import AuthType, User
from Pytre_Server.server_state import PASSWORD_MAX_CONCURRENT_CHECKS, ServerResources


@contextmanager
def every_password_slot_taken(resources: ServerResources, monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Holds every password check slot, as if PASSWORD_MAX_CONCURRENT_CHECKS checks were running - used by the
    /login and /admin/login tests. The wait for a free slot is shortened so a refusal comes fast.
    Only one request may wait on the slots: TestClient runs each request in an event loop of its own, and an
    asyncio semaphore stays bound to the first loop a request waited in."""

    async def take_every_slot() -> None:
        for _ in range(PASSWORD_MAX_CONCURRENT_CHECKS):
            await resources.password_semaphore.acquire()  # never waits: the slots are free

    monkeypatch.setattr("Pytre_Server.server_state.PASSWORD_CHECK_WAIT_SECS", 0.05)
    asyncio.run(take_every_slot())
    try:
        yield
    finally:
        for _ in range(PASSWORD_MAX_CONCURRENT_CHECKS):
            resources.password_semaphore.release()


IP_THRESHOLD = 3  # replaces IP_MAX_ATTEMPTS in the route tests, to keep them short


def lower_ip_threshold(client: TestClient) -> None:
    """Throttles the client's address after IP_THRESHOLD failures instead of IP_MAX_ATTEMPTS - the threshold is
    read when the app's ServerResources is built, so it is set on that instance."""
    resources = cast(ServerResources, cast(FastAPI, client.app).state.server.resources)
    resources.login_throttle.ip.max_attempts = IP_THRESHOLD


def client_from(client: TestClient, host: str) -> TestClient:
    """A client of the same app as `client`, connecting from the address `host`."""
    return TestClient(client.app, client=(host, 50000))


def fail_from_one_address(client: TestClient, count: int) -> None:
    """`count` failed /login calls from the client's address, each for another unknown username - never enough to
    throttle a username."""
    for i in range(count):
        client.post("/login", json={"username": f"no-such-user-{i}", "password": "wrong-password"})


def event_loop_is_running() -> bool:
    """False in a worker thread - lets a spy tell whether a blocking call was kept off the event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


SSO_ISSUER = "https://idp.example.com"
SSO_CLIENT_ID = "pytre-client"
SSO_USERNAME = "sso_user"
SSO_KID = "test-key"

_RealAsyncClient = httpx2.AsyncClient  # captured before any monkeypatching, see install_idp_transport


def make_oidc_provider(app_ctx, **overrides) -> OidcProvider:
    provider = OidcProvider(app_ctx.oidc_providers)
    provider.issuer = SSO_ISSUER
    provider.client_id = SSO_CLIENT_ID
    provider.client_secret = "s3cret"
    provider.enabled = True
    provider.public_base_url = "http://testserver"
    for name, value in overrides.items():
        setattr(provider, name, value)
    provider.save()
    return provider


def make_oidc_user(app_ctx, username: str = SSO_USERNAME, admin: bool = True, **overrides) -> User:
    user = User(app_ctx.users, detect_user=False)
    user.username = username
    user.auth_type = AuthType.OIDC
    user.admin = admin
    for name, value in overrides.items():
        setattr(user, name, value)
    user.save()
    return user


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def install_idp_transport(monkeypatch, rsa_key):
    """Fakes the identity provider's discovery + JWKS endpoints - the only two network calls
    OidcClient itself makes."""
    jwk = json.loads(RSAAlgorithm.to_jwk(rsa_key.public_key()))
    jwk["kid"] = SSO_KID

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx2.Response(
                200,
                json={
                    "issuer": SSO_ISSUER,
                    "authorization_endpoint": f"{SSO_ISSUER}/auth",
                    "token_endpoint": f"{SSO_ISSUER}/token",
                    "jwks_uri": f"{SSO_ISSUER}/jwks",
                },
            )
        if request.url.path.endswith("/jwks"):
            return httpx2.Response(200, json={"keys": [jwk]})
        return httpx2.Response(404)

    def factory(*args, **kwargs):
        return _RealAsyncClient(transport=httpx2.MockTransport(handler))

    monkeypatch.setattr("Pytre_Core.oidc_client.httpx2.AsyncClient", factory)


def make_id_token(rsa_key, claims: dict) -> str:
    now = int(time.time())
    base = {"iss": SSO_ISSUER, "aud": SSO_CLIENT_ID, "iat": now, "exp": now + 300}
    base.update(claims)
    return jwt.encode(base, rsa_key, algorithm="RS256", headers={"kid": SSO_KID})


class TokenExchangeState:
    """Controls what AsyncOAuth2Client.fetch_token() returns/raises during the code exchange in
    handle_callback() - a test mutates `.claims`/`.exc` before hitting the callback route.
    create_authorization_url() (used by start_login()) is left real and untouched - only
    fetch_token()/aclose() are patched below, since start_login() and handle_callback() share
    the same AsyncOAuth2Client class within a single end-to-end test."""

    def __init__(self, rsa_key) -> None:
        self._rsa_key = rsa_key
        self.claims: dict = {"sub": "sub-123", "preferred_username": SSO_USERNAME}
        self.exc: BaseException | None = None


@pytest.fixture
def fake_token_exchange(monkeypatch, rsa_key) -> TokenExchangeState:
    state = TokenExchangeState(rsa_key)

    async def fake_fetch_token(self, _token_endpoint: str, **_kwargs) -> dict:
        if state.exc is not None:
            raise state.exc
        # a real IdP returns a refresh_token here too, since start_login() always requests
        # offline_access - tests that only care about the ID token can ignore this key
        return {"id_token": make_id_token(state._rsa_key, state.claims), "refresh_token": "idp-refresh-token"}

    async def fake_aclose(self) -> None:
        pass

    monkeypatch.setattr(AsyncOAuth2Client, "fetch_token", fake_fetch_token)
    monkeypatch.setattr(AsyncOAuth2Client, "aclose", fake_aclose)
    return state


def extract_state_token(auth_url: str) -> str:
    return parse_qs(urlparse(auth_url).query)["state"][0]


class RefreshGrantState:
    """Controls what AsyncOAuth2Client.refresh_token() returns/raises during OIDC re-verification
    in Pytre_Server.oidc.verify_still_active() - a test mutates `.response`/`.exc` before hitting
    the /refresh route. Same monkeypatch idiom as TokenExchangeState/fake_token_exchange, applied
    to refresh_token() instead of fetch_token()."""

    def __init__(self) -> None:
        self.response: dict = {"access_token": "idp-access-token", "refresh_token": "new-refresh-token"}
        self.exc: BaseException | None = None
        self.calls: int = 0  # lets a test assert the fake IdP was/wasn't actually contacted


@pytest.fixture
def fake_refresh_grant(monkeypatch) -> RefreshGrantState:
    state = RefreshGrantState()

    async def fake_refresh_token(self, _token_endpoint: str, refresh_token: str | None = None, **_kwargs) -> dict:
        state.calls += 1
        if state.exc is not None:
            raise state.exc
        return state.response

    async def fake_aclose(self) -> None:
        pass

    monkeypatch.setattr(AsyncOAuth2Client, "refresh_token", fake_refresh_token)
    monkeypatch.setattr(AsyncOAuth2Client, "aclose", fake_aclose)
    return state
