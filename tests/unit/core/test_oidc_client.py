"""
Tests for Pytre_Core/oidc_client.py - OidcClient (discovery + ID token validation, with caching)
and new_token().

No real network or Docker dependency: discover()/_get_jwks() are the only two places that make an
HTTP call (both through _fetch_json), so every httpx2.AsyncClient created in that module is
redirected to an in-memory httpx2.MockTransport (see `install_transport`) driven by a handler each
test controls. ID tokens are real, RS256-signed JWTs built against a real RSA key generated once
for the module - exercising jwt.decode()'s actual verification, not a stand-in.
"""

import json
import time

import httpx2
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from Pytre_Core.oidc_client import (
    ALLOWED_ALGORITHMS,
    OidcClient,
    OidcDiscovery,
    OidcDiscoveryError,
    OidcTokenError,
    new_token,
)

pytestmark = pytest.mark.unit

ISSUER = "https://idp.example.com"
CLIENT_ID = "pytre-client"
KID = "test-key"


@pytest.fixture
def anyio_backend():
    return "asyncio"


_RealAsyncClient = httpx2.AsyncClient  # captured before any monkeypatching, see install_transport


@pytest.fixture
def install_transport(monkeypatch):
    """Redirects every httpx2.AsyncClient created inside oidc_client.py to an in-memory
    MockTransport driven by `handler` - discover()/_get_jwks() are tested for real (real JSON
    parsing, real status-code handling), only the socket is faked."""

    def install(handler):
        def factory(*args, **kwargs):
            return _RealAsyncClient(transport=httpx2.MockTransport(handler))

        monkeypatch.setattr("Pytre_Core.oidc_client.httpx2.AsyncClient", factory)

    return install


def _responder(status_code: int, *, json_body: dict | None = None, text: str | None = None, calls=None):
    """A MockTransport handler that always answers the same thing, and (if `calls` is a list)
    records every request URL it received - used to prove a second call was/wasn't a cache hit."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        if calls is not None:
            calls.append(str(request.url))
        if text is not None:
            return httpx2.Response(status_code, text=text)
        return httpx2.Response(status_code, json=json_body)

    return handler


def _raiser(exc: Exception):
    def handler(request: httpx2.Request):
        raise exc

    return handler


def _discovery_doc(issuer: str = ISSUER, **overrides) -> dict:
    doc = {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/auth",
        "token_endpoint": f"{issuer}/token",
        "jwks_uri": f"{issuer}/jwks",
    }
    doc.update(overrides)
    return doc


# ================================================================================
# discover()
# ================================================================================


@pytest.mark.anyio
async def test_discover_returns_the_announced_issuer_not_the_queried_one(install_transport):
    """Some IdPs' issuer differs from the URL used to reach them (a trailing slash, or worse) -
    OidcDiscovery.issuer must be what the document itself says."""
    requested_urls = []
    install_transport(_responder(200, json_body=_discovery_doc(issuer=ISSUER), calls=requested_urls))

    discovery = await OidcClient().discover(ISSUER + "/")  # trailing slash on the queried issuer

    assert discovery.issuer == ISSUER
    assert requested_urls == [f"{ISSUER}/.well-known/openid-configuration"]  # no double slash


@pytest.mark.anyio
async def test_discover_raises_on_missing_required_fields(install_transport):
    install_transport(_responder(200, json_body={"issuer": ISSUER, "authorization_endpoint": "x"}))

    with pytest.raises(OidcDiscoveryError, match="token_endpoint.*jwks_uri"):
        await OidcClient().discover(ISSUER)


@pytest.mark.anyio
async def test_discover_raises_on_http_error(install_transport):
    install_transport(_responder(500, text="server error"))

    with pytest.raises(OidcDiscoveryError):
        await OidcClient().discover(ISSUER)


@pytest.mark.anyio
async def test_discover_raises_on_a_network_error(install_transport):
    install_transport(_raiser(httpx2.ConnectError("refused")))

    with pytest.raises(OidcDiscoveryError):
        await OidcClient().discover(ISSUER)


@pytest.mark.anyio
async def test_discover_raises_on_invalid_json(install_transport):
    install_transport(_responder(200, text="not json"))

    with pytest.raises(OidcDiscoveryError):
        await OidcClient().discover(ISSUER)


@pytest.mark.anyio
async def test_discover_caches_and_avoids_a_second_request(install_transport):
    calls = []
    install_transport(_responder(200, json_body=_discovery_doc(), calls=calls))
    client = OidcClient()

    first = await client.discover(ISSUER)
    second = await client.discover(ISSUER)

    assert second is first
    assert len(calls) == 1


@pytest.mark.anyio
async def test_discover_refetches_once_the_cache_entry_has_expired(install_transport):
    calls = []
    install_transport(_responder(200, json_body=_discovery_doc(), calls=calls))
    client = OidcClient()

    first = await client.discover(ISSUER)
    client._discovery_cache[ISSUER].expires_at = time.monotonic() - 1  # force expiry
    second = await client.discover(ISSUER)

    assert second == first
    assert second is not first
    assert len(calls) == 2


# ================================================================================
# validate_id_token() - fixtures: a real RSA key, its JWK, and a signed-token builder
# ================================================================================


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def other_rsa_key():
    """A second, unrelated key - used to build a token whose signature doesn't match the JWKS."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def jwk(rsa_key) -> dict:
    data = json.loads(RSAAlgorithm.to_jwk(rsa_key.public_key()))
    data["kid"] = KID
    return data


@pytest.fixture
def jwks(jwk) -> dict:
    return {"keys": [jwk]}


@pytest.fixture
def discovery() -> OidcDiscovery:
    return OidcDiscovery(
        issuer=ISSUER,
        authorization_endpoint=f"{ISSUER}/auth",
        token_endpoint=f"{ISSUER}/token",
        jwks_uri=f"{ISSUER}/jwks",
    )


def _make_token(key, *, kid: str | None = KID, algorithm: str = "RS256", **claim_overrides) -> str:
    now = int(time.time())
    claims = {"iss": ISSUER, "sub": "user-123", "aud": CLIENT_ID, "exp": now + 300, "iat": now}
    claims.update(claim_overrides)
    headers = {"kid": kid} if kid is not None else {}
    return jwt.encode(claims, key, algorithm=algorithm, headers=headers)


@pytest.mark.anyio
async def test_validate_id_token_accepts_a_correctly_signed_token(install_transport, rsa_key, jwks, discovery):
    install_transport(_responder(200, json_body=jwks))
    token = _make_token(rsa_key, nonce="the-nonce")

    claims = await OidcClient().validate_id_token(token, discovery, client_id=CLIENT_ID, nonce="the-nonce")

    assert claims["sub"] == "user-123"


@pytest.mark.anyio
async def test_validate_id_token_ignores_nonce_when_none_is_passed(install_transport, rsa_key, jwks, discovery):
    """Bearer mode (access tokens, no nonce in play) must not be forced to pass one."""
    install_transport(_responder(200, json_body=jwks))
    token = _make_token(rsa_key)  # no nonce claim at all

    claims = await OidcClient().validate_id_token(token, discovery, client_id=CLIENT_ID)

    assert "nonce" not in claims


@pytest.mark.anyio
async def test_validate_id_token_rejects_a_wrong_signature(install_transport, other_rsa_key, jwks, discovery):
    """Signed by a different key than the one published in the JWKS, but claiming the same kid.
    PyJWT's own exception is preserved as __cause__ - see the module docstring/OidcTokenError
    for why callers should only ever need to catch OidcTokenError, never import jwt themselves."""
    install_transport(_responder(200, json_body=jwks))
    token = _make_token(other_rsa_key)

    with pytest.raises(OidcTokenError) as exc_info:
        await OidcClient().validate_id_token(token, discovery, client_id=CLIENT_ID)
    assert isinstance(exc_info.value.__cause__, jwt.InvalidSignatureError)


@pytest.mark.anyio
async def test_validate_id_token_rejects_a_disallowed_algorithm(install_transport, jwks, discovery):
    """HS256 isn't in ALLOWED_ALGORITHMS - a client (which knows client_id, and for a public client
    has no secret at all) mustn't be able to sign its own token."""
    install_transport(_responder(200, json_body=jwks))
    token = _make_token("a-long-enough-shared-secret-value", algorithm="HS256", kid=None)

    with pytest.raises(OidcTokenError) as exc_info:
        await OidcClient().validate_id_token(token, discovery, client_id=CLIENT_ID)
    assert isinstance(exc_info.value.__cause__, jwt.InvalidAlgorithmError)


@pytest.mark.anyio
async def test_validate_id_token_rejects_the_wrong_issuer(install_transport, rsa_key, jwks, discovery):
    install_transport(_responder(200, json_body=jwks))
    token = _make_token(rsa_key, iss="https://evil.example.com")

    with pytest.raises(OidcTokenError) as exc_info:
        await OidcClient().validate_id_token(token, discovery, client_id=CLIENT_ID)
    assert isinstance(exc_info.value.__cause__, jwt.InvalidIssuerError)


@pytest.mark.anyio
async def test_validate_id_token_rejects_the_wrong_audience(install_transport, rsa_key, jwks, discovery):
    install_transport(_responder(200, json_body=jwks))
    token = _make_token(rsa_key, aud="someone-else")

    with pytest.raises(OidcTokenError) as exc_info:
        await OidcClient().validate_id_token(token, discovery, client_id=CLIENT_ID)
    assert isinstance(exc_info.value.__cause__, jwt.InvalidAudienceError)


@pytest.mark.anyio
async def test_validate_id_token_rejects_an_expired_token(install_transport, rsa_key, jwks, discovery):
    install_transport(_responder(200, json_body=jwks))
    now = int(time.time())
    token = _make_token(rsa_key, iat=now - 1000, exp=now - 700)  # well past the default leeway

    with pytest.raises(OidcTokenError) as exc_info:
        await OidcClient().validate_id_token(token, discovery, client_id=CLIENT_ID)
    assert isinstance(exc_info.value.__cause__, jwt.ExpiredSignatureError)


@pytest.mark.anyio
async def test_validate_id_token_accepts_a_recently_expired_token_within_leeway(
    install_transport, rsa_key, jwks, discovery
):
    install_transport(_responder(200, json_body=jwks))
    now = int(time.time())
    token = _make_token(rsa_key, iat=now - 400, exp=now - 5)  # expired 5s ago, well under 300s leeway

    claims = await OidcClient().validate_id_token(token, discovery, client_id=CLIENT_ID, leeway=300)

    assert claims["sub"] == "user-123"


@pytest.mark.anyio
async def test_validate_id_token_rejects_a_mismatched_nonce(install_transport, rsa_key, jwks, discovery):
    install_transport(_responder(200, json_body=jwks))
    token = _make_token(rsa_key, nonce="expected")

    with pytest.raises(OidcTokenError, match="nonce"):
        await OidcClient().validate_id_token(token, discovery, client_id=CLIENT_ID, nonce="not-the-same")


@pytest.mark.anyio
async def test_validate_id_token_rejects_an_unknown_kid(install_transport, rsa_key, jwks, discovery):
    install_transport(_responder(200, json_body=jwks))
    token = _make_token(rsa_key, kid="not-in-the-jwks")

    with pytest.raises(OidcTokenError, match="kid"):
        await OidcClient().validate_id_token(token, discovery, client_id=CLIENT_ID)


@pytest.mark.anyio
async def test_validate_id_token_rejects_a_missing_kid_when_several_keys_are_published(
    install_transport, rsa_key, other_rsa_key, jwk, discovery
):
    """A single published key can be used without a kid (see _find_jwk) - several can't, there's
    no way to guess which one signed the token."""
    other_jwk = json.loads(RSAAlgorithm.to_jwk(other_rsa_key.public_key()))
    other_jwk["kid"] = "other-key"
    install_transport(_responder(200, json_body={"keys": [jwk, other_jwk]}))
    token = _make_token(rsa_key, kid=None)

    with pytest.raises(OidcTokenError, match="kid"):
        await OidcClient().validate_id_token(token, discovery, client_id=CLIENT_ID)


@pytest.mark.anyio
async def test_validate_id_token_rejects_a_malformed_token(install_transport, jwks, discovery):
    install_transport(_responder(200, json_body=jwks))

    with pytest.raises(OidcTokenError, match="Malformed"):
        await OidcClient().validate_id_token("not-a-jwt-at-all", discovery, client_id=CLIENT_ID)


@pytest.mark.anyio
async def test_get_jwks_caches_and_avoids_a_second_request(install_transport, jwks, discovery, rsa_key):
    calls = []
    install_transport(_responder(200, json_body=jwks, calls=calls))
    client = OidcClient()
    token = _make_token(rsa_key)

    await client.validate_id_token(token, discovery, client_id=CLIENT_ID)
    await client.validate_id_token(token, discovery, client_id=CLIENT_ID)

    assert len(calls) == 1


def test_allowed_algorithms_excludes_none_and_hmac():
    """Regression guard: "none" and HS* must never be added back - both would let a token holder
    forge its own signature (see validate_id_token's docstring)."""
    assert "none" not in ALLOWED_ALGORITHMS
    assert not any(alg.startswith("HS") for alg in ALLOWED_ALGORITHMS)


# ================================================================================
# new_token()
# ================================================================================


def test_new_token_is_long_enough_for_pkce():
    """RFC 7636 requires the code_verifier to be 43-128 characters."""
    token = new_token()
    assert 43 <= len(token) <= 128


def test_new_token_is_not_predictable():
    assert new_token() != new_token()
