"""
Generic OpenID Connect helpers shared by the server.
Nothing is app specific or web-framework specific.

Discovery documents and JWKS are cached per issuer (OidcClient), since both change
rarely but are looked up on every login/validation.
httpx2 is used to fetch so event loop is not blocked. jwt.PyJWKClient fetch use blocking urllib.
"""

import secrets
import time
from dataclasses import dataclass

import httpx2
import jwt


_DISCOVERY_CACHE_SECS = 60 * 60  # discovery documents change essentially never
_JWKS_CACHE_SECS = 60 * 10  # signing keys rotate occasionally

ALLOWED_ALGORITHMS = (  # Asymmetric algorithms only - a client shouldn't be able to sign a token.
    "RS256",
    "RS384",
    "RS512",
    "ES256",
    "ES384",
    "ES512",
    "PS256",
    "PS384",
    "PS512",
)


class OidcError(Exception):
    """Base for every error this module raises - discovery, JWKS, and token validation failures."""


class OidcDiscoveryError(OidcError):
    """Raised when the discovery document can't be fetched or is missing a required field."""


class OidcTokenError(OidcError):
    """Raised when an ID token fails signature, claim, or nonce validation."""


@dataclass(frozen=True)
class OidcDiscovery:
    """The subset of a discovery document actually used. `issuer` is the value the document
    itself announces - not the URL used to fetch it (some IdPs' issuer differs by a trailing slash,
    or - Entra ID's multi-tenant endpoints - isn't a comparable literal string at all). Every token
    `iss` claim must be checked against this, not against the configured issuer."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


@dataclass
class _CacheEntry[T]:
    value: T
    expires_at: float


class OidcClient:
    """Fetches and caches one issuer's discovery document and signing keys.
    One instance is meant to be shared and long-lived to avoid unnecessary refetch."""

    def __init__(self, timeout: float = 5.0) -> None:
        self._timeout = timeout
        self._discovery_cache: dict[str, _CacheEntry[OidcDiscovery]] = {}
        self._jwks_cache: dict[str, _CacheEntry[dict]] = {}

    async def discover(self, issuer: str) -> OidcDiscovery:
        """Fetches (or returns the cached) discovery document for `issuer`."""
        # use and return cached value if possible
        cached = self._get_cache(self._discovery_cache, issuer)
        if cached is not None:
            return cached

        # fetch OIDC well-known configuration from OIDC url
        url = issuer.rstrip("/") + "/.well-known/openid-configuration"
        data = await self._fetch_json(url, OidcDiscoveryError)

        # check retrieved config
        missing = [f for f in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri") if f not in data]
        if missing:
            raise OidcDiscoveryError(f"Discovery document from {url} is missing: {', '.join(missing)}")

        # create and cache discovery document
        discovery = OidcDiscovery(
            issuer=data["issuer"],
            authorization_endpoint=data["authorization_endpoint"],
            token_endpoint=data["token_endpoint"],
            jwks_uri=data["jwks_uri"],
        )
        self._set_cache(self._discovery_cache, issuer, discovery, _DISCOVERY_CACHE_SECS)

        return discovery

    async def validate_id_token(
        self, id_token: str, discovery: OidcDiscovery, *, client_id: str, nonce: str | None = None, leeway: int = 300
    ) -> dict:
        """Verifies signature, `iss`, `aud`, `exp`/`iat` (with `leeway` seconds of tolerance for
        clock drift) and, when given, `nonce`.
        Raises OidcTokenError/jwt.InvalidTokenError naming what failed. Returns the verified claims -
        never call this without using its result, an unverified jwt.decode(verify=False) elsewhere would
        defeat the point of validating here."""
        # get header from token
        try:
            header_dict = jwt.get_unverified_header(id_token)
        except jwt.InvalidTokenError as e:
            raise OidcTokenError(f"Malformed ID token: {e}") from e

        # retrieve signing key
        jwks = await self._get_jwks(discovery.jwks_uri)
        jwk_dict = self._find_jwk(jwks, header_dict.get("kid"))
        if jwk_dict is None:
            raise OidcTokenError(f"No matching signing key for kid={header_dict.get('kid')}")
        signing_key = jwt.PyJWK(jwk_dict).key

        # decode claims
        try:
            claims = jwt.decode(
                id_token,
                signing_key,
                algorithms=list(ALLOWED_ALGORITHMS),
                issuer=discovery.issuer,
                audience=client_id,
                leeway=leeway,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except jwt.InvalidTokenError as e:
            raise OidcTokenError(f"ID token validation failed: {e}") from e

        if nonce is not None and claims.get("nonce") != nonce:
            raise OidcTokenError("ID token nonce doesn't match")

        return claims

    async def _fetch_json(self, url: str, error_cls: type[OidcError]) -> dict:
        try:
            async with httpx2.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.json()
        except httpx2.HTTPStatusError as e:
            raise error_cls(f"Request to {url} failed: HTTP {e.response.status_code}") from e
        except httpx2.RequestError as e:
            raise error_cls(f"Request to {url} failed: {e}") from e
        except ValueError as e:  # invalid JSON
            raise error_cls(f"Response from {url} isn't valid JSON") from e

    async def _get_jwks(self, jwks_uri: str) -> dict:
        cached = self._get_cache(self._jwks_cache, jwks_uri)
        if cached is not None:
            return cached

        jwks_dict = await self._fetch_json(jwks_uri, OidcTokenError)
        self._set_cache(self._jwks_cache, jwks_uri, jwks_dict, _JWKS_CACHE_SECS)
        return jwks_dict

    @staticmethod
    def _find_jwk(jwks_dict: dict, kid: str | None) -> dict | None:
        keys: list[dict] = jwks_dict.get("keys", [])

        if kid is not None:
            match = next((k for k in keys if k.get("kid") == kid), None)
            if match is not None:
                return match
            return None

        return keys[0] if len(keys) == 1 else None

    def _get_cache[T](self, cache: dict[str, _CacheEntry[T]], key: str) -> T | None:
        entry = cache.get(key)
        if entry is not None and entry.expires_at > time.monotonic():
            return entry.value

        cache.pop(key, None)

        return None

    def _set_cache[T](self, cache: dict[str, _CacheEntry[T]], key: str, value: T, ttl: float) -> None:
        cache[key] = _CacheEntry(value=value, expires_at=time.monotonic() + ttl)


def new_token(length_bytes: int = 32) -> str:
    """A cryptographically random URL-safe token, for state/nonce/the PKCE code_verifier (RFC 7636
    requires 43-128 characters; 32 bytes -> 43 chars). The S256 code_challenge itself is computed
    by the OAuth2 client library (Authlib) from this value, not here."""
    return secrets.token_urlsafe(length_bytes)
