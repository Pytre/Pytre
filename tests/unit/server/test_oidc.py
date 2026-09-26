"""
Tests for Pytre_Server/oidc.py - the server-side OIDC relying party flow (start_login/handle_callback)
and the user-linking logic (link_user).

start_login()/handle_callback() are tested with the IdP-facing boundaries replaced by fakes, at the
two points that already have their own dedicated coverage: ServerResources.oidc_client (see
tests/unit/core/test_oidc_client.py) and Authlib's AsyncOAuth2Client, whose code exchange is the
only network call either function makes - create_authorization_url() does none.
link_user() is tested against a real KeePass database (a per-test copy of tests/resources/Pytre_test.db),
like tests/unit/core/test_oidc_providers.py, since what matters there is genuine User/AuthType/save()
behavior a mock would only echo.
"""

import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import httpx2
import pytest
from authlib.oauth2.rfc6749.errors import InvalidGrantError

import Pytre_Core.kee as kee_module
from Pytre_Core.kee import Kee
from Pytre_Core.oidc_client import OidcClient, OidcDiscovery, OidcDiscoveryError, OidcTokenError
from Pytre_Core.oidc_providers import OidcProvider, OidcProviders
from Pytre_Core.users import AuthType, OidcLink, User, Users
from Pytre_Server.oidc import OidcLoginError, OidcReverifyError, handle_callback, link_user, start_login, verify_still_active
from Pytre_Server.server_state import ServerResources

pytestmark = pytest.mark.unit

TEST_DB_PATH = Path(__file__).parent.parent.parent / "resources" / "Pytre_test.db"
TEST_DB_PASSWORD = "password"

ISSUER = "https://idp.example.com"
REDIRECT_URI = "https://pytre.example.com/admin/login/sso/callback"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _discovery(**overrides) -> OidcDiscovery:
    data = {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/auth",
        "token_endpoint": f"{ISSUER}/token",
        "jwks_uri": f"{ISSUER}/jwks",
    }
    data.update(overrides)
    return OidcDiscovery(**data)


def _provider(**overrides) -> OidcProvider:
    provider = OidcProvider(Mock(spec=OidcProviders))
    provider.issuer = ISSUER
    provider.client_id = "pytre-client"
    provider.client_secret = "s3cret"
    provider.enabled = True
    for name, value in overrides.items():
        setattr(provider, name, value)
    return provider


def _oidc_providers(*providers: OidcProvider) -> OidcProviders:
    mock = Mock(spec=OidcProviders)
    mock.get_all_providers.return_value = {p.issuer: p for p in providers}
    return mock


def _oidc_client_mock(*, discover=None, discover_exc=None, validate=None, validate_exc=None) -> Mock:
    """discover()/validate_id_token() are async on OidcClient, so Mock(spec=...) auto-creates
    AsyncMock for them."""
    oidc_client = Mock(spec=OidcClient)
    if discover_exc is not None:
        oidc_client.discover.side_effect = discover_exc
    else:
        oidc_client.discover.return_value = discover if discover is not None else _discovery()

    if validate_exc is not None:
        oidc_client.validate_id_token.side_effect = validate_exc
    else:
        oidc_client.validate_id_token.return_value = validate if validate is not None else {"sub": "sub-123"}

    return oidc_client


def _resources(**kwargs) -> ServerResources:
    return ServerResources(oidc_client=_oidc_client_mock(**kwargs))


class _FakeOAuth2Client:
    def __init__(self, factory: "_FakeOAuth2ClientFactory") -> None:
        self._factory = factory
        self.aclose_called = False
        self.fetch_token_kwargs: dict | None = None

    async def fetch_token(self, _token_endpoint: str, **kwargs) -> dict:
        self.fetch_token_kwargs = kwargs
        if self._factory.exc is not None:
            raise self._factory.exc
        return self._factory.result

    async def refresh_token(self, _token_endpoint: str, refresh_token: str | None = None, **kwargs) -> dict:
        self.fetch_token_kwargs = {"refresh_token": refresh_token, **kwargs}
        if self._factory.exc is not None:
            raise self._factory.exc
        return self._factory.result

    async def aclose(self) -> None:
        self.aclose_called = True


class _FakeOAuth2ClientFactory:
    """Replaces Authlib's AsyncOAuth2Client (used only for the code exchange in handle_callback())
    - each call to it (one per handle_callback()) returns a new fake client sharing this factory's
    result/exc, and is recorded in `instances` so a test can inspect it afterward."""

    def __init__(self) -> None:
        self.result: dict = {"id_token": "fake-id-token"}
        self.exc: BaseException | None = None
        self.instances: list[_FakeOAuth2Client] = []

    def __call__(self, *_args, **_kwargs) -> _FakeOAuth2Client:
        client = _FakeOAuth2Client(self)
        self.instances.append(client)
        return client


@pytest.fixture
def oauth_client_factory(monkeypatch) -> _FakeOAuth2ClientFactory:
    factory = _FakeOAuth2ClientFactory()
    monkeypatch.setattr("Pytre_Server.oidc.AsyncOAuth2Client", factory)
    return factory


# ================================================================================
# start_login()
# ================================================================================


@pytest.mark.anyio
async def test_start_login_returns_an_authorization_url_carrying_the_state_and_pkce_challenge():
    resources = _resources()
    provider = _provider()

    url = await start_login(resources, provider, redirect_uri=REDIRECT_URI, next_path="/admin")

    assert url.startswith(f"{ISSUER}/auth?")
    assert "code_challenge=" in url
    assert "code_challenge_method=S256" in url
    (state_token,) = resources.oidc_pending.keys()
    assert f"state={state_token}" in url


@pytest.mark.anyio
async def test_start_login_registers_a_pending_login_matching_the_provider_and_next_path():
    resources = _resources()
    provider = _provider(issuer="https://other-idp.example.com")

    await start_login(resources, provider, redirect_uri=REDIRECT_URI, next_path="/admin/servers")

    (pending,) = resources.oidc_pending.values()
    assert pending.provider_issuer == provider.issuer
    assert pending.next_path == "/admin/servers"
    assert pending.nonce and pending.code_verifier


@pytest.mark.anyio
async def test_start_login_generates_a_fresh_nonce_and_verifier_on_each_call():
    resources = _resources()
    provider = _provider()

    await start_login(resources, provider, redirect_uri=REDIRECT_URI, next_path="/admin")
    await start_login(resources, provider, redirect_uri=REDIRECT_URI, next_path="/admin")

    first, second = resources.oidc_pending.values()
    assert first.nonce != second.nonce
    assert first.code_verifier != second.code_verifier


@pytest.mark.anyio
async def test_start_login_wraps_a_discovery_failure():
    resources = _resources(discover_exc=OidcDiscoveryError("unreachable"))
    provider = _provider()

    with pytest.raises(OidcLoginError) as exc_info:
        await start_login(resources, provider, redirect_uri=REDIRECT_URI, next_path="/admin")
    assert isinstance(exc_info.value.__cause__, OidcDiscoveryError)


@pytest.mark.anyio
async def test_start_login_registers_no_pending_login_when_discovery_fails():
    resources = _resources(discover_exc=OidcDiscoveryError("unreachable"))
    provider = _provider()

    with pytest.raises(OidcLoginError):
        await start_login(resources, provider, redirect_uri=REDIRECT_URI, next_path="/admin")

    assert resources.oidc_pending == {}


# ================================================================================
# handle_callback() - pending state / provider lookup
# ================================================================================


@pytest.mark.anyio
async def test_handle_callback_rejects_an_unknown_state():
    resources = _resources()

    with pytest.raises(OidcLoginError, match="expired or was already used"):
        await handle_callback(resources, _oidc_providers(), Mock(spec=Users), "never-issued", "code", REDIRECT_URI)


@pytest.mark.anyio
async def test_handle_callback_rejects_a_reused_state(oauth_client_factory, monkeypatch):
    resources = _resources()
    provider = _provider()
    state_token = resources.oidc_start_pending_login(provider.issuer, "n", "v", "/admin")
    monkeypatch.setattr("Pytre_Server.oidc.link_user", Mock(return_value=Mock(spec=User)))

    await handle_callback(resources, _oidc_providers(provider), Mock(spec=Users), state_token, "code", REDIRECT_URI)

    with pytest.raises(OidcLoginError, match="expired or was already used"):
        await handle_callback(
            resources, _oidc_providers(provider), Mock(spec=Users), state_token, "code", REDIRECT_URI
        )


@pytest.mark.anyio
async def test_handle_callback_rejects_when_the_provider_is_no_longer_known():
    resources = _resources()
    state_token = resources.oidc_start_pending_login("https://gone.example.com", "n", "v", "/admin")

    with pytest.raises(OidcLoginError, match="no longer usable"):
        await handle_callback(resources, _oidc_providers(), Mock(spec=Users), state_token, "code", REDIRECT_URI)


@pytest.mark.anyio
async def test_handle_callback_rejects_when_the_provider_has_been_disabled_since_start_login():
    resources = _resources()
    provider = _provider(enabled=False)
    state_token = resources.oidc_start_pending_login(provider.issuer, "n", "v", "/admin")

    with pytest.raises(OidcLoginError, match="no longer usable"):
        await handle_callback(
            resources, _oidc_providers(provider), Mock(spec=Users), state_token, "code", REDIRECT_URI
        )


@pytest.mark.anyio
async def test_handle_callback_consumes_the_pending_login_even_when_it_fails():
    resources = _resources()
    state_token = resources.oidc_start_pending_login("https://gone.example.com", "n", "v", "/admin")

    with pytest.raises(OidcLoginError):
        await handle_callback(resources, _oidc_providers(), Mock(spec=Users), state_token, "code", REDIRECT_URI)

    assert state_token not in resources.oidc_pending


# ================================================================================
# handle_callback() - discovery / code exchange / ID token validation
# ================================================================================


@pytest.mark.anyio
async def test_handle_callback_wraps_a_discovery_failure():
    resources = _resources(discover_exc=OidcDiscoveryError("down"))
    provider = _provider()
    state_token = resources.oidc_start_pending_login(provider.issuer, "n", "v", "/admin")

    with pytest.raises(OidcLoginError) as exc_info:
        await handle_callback(
            resources, _oidc_providers(provider), Mock(spec=Users), state_token, "code", REDIRECT_URI
        )
    assert isinstance(exc_info.value.__cause__, OidcDiscoveryError)


@pytest.mark.anyio
async def test_handle_callback_exchanges_the_code_with_the_stored_pkce_verifier(oauth_client_factory, monkeypatch):
    resources = _resources()
    provider = _provider()
    state_token = resources.oidc_start_pending_login(provider.issuer, "n", "the-code-verifier", "/admin")
    monkeypatch.setattr("Pytre_Server.oidc.link_user", Mock(return_value=Mock(spec=User)))

    await handle_callback(
        resources, _oidc_providers(provider), Mock(spec=Users), state_token, "the-code", REDIRECT_URI
    )

    assert oauth_client_factory.instances[0].fetch_token_kwargs == {
        "code": "the-code",
        "code_verifier": "the-code-verifier",
    }


@pytest.mark.anyio
async def test_handle_callback_wraps_a_code_exchange_failure_and_still_closes_the_client(oauth_client_factory):
    resources = _resources()
    provider = _provider()
    state_token = resources.oidc_start_pending_login(provider.issuer, "n", "v", "/admin")
    oauth_client_factory.exc = InvalidGrantError()

    with pytest.raises(OidcLoginError, match="exchange"):
        await handle_callback(
            resources, _oidc_providers(provider), Mock(spec=Users), state_token, "code", REDIRECT_URI
        )

    assert oauth_client_factory.instances[0].aclose_called is True


@pytest.mark.anyio
async def test_handle_callback_closes_the_client_on_success_too(oauth_client_factory, monkeypatch):
    resources = _resources()
    provider = _provider()
    state_token = resources.oidc_start_pending_login(provider.issuer, "n", "v", "/admin")
    monkeypatch.setattr("Pytre_Server.oidc.link_user", Mock(return_value=Mock(spec=User)))

    await handle_callback(resources, _oidc_providers(provider), Mock(spec=Users), state_token, "code", REDIRECT_URI)

    assert oauth_client_factory.instances[0].aclose_called is True


@pytest.mark.anyio
async def test_handle_callback_rejects_a_token_response_without_an_id_token(oauth_client_factory):
    resources = _resources()
    provider = _provider()
    state_token = resources.oidc_start_pending_login(provider.issuer, "n", "v", "/admin")
    oauth_client_factory.result = {"access_token": "at-only"}

    with pytest.raises(OidcLoginError, match="ID token"):
        await handle_callback(
            resources, _oidc_providers(provider), Mock(spec=Users), state_token, "code", REDIRECT_URI
        )


@pytest.mark.anyio
async def test_handle_callback_wraps_an_invalid_id_token(oauth_client_factory):
    resources = _resources(validate_exc=OidcTokenError("bad signature"))
    provider = _provider()
    state_token = resources.oidc_start_pending_login(provider.issuer, "n", "v", "/admin")

    with pytest.raises(OidcLoginError) as exc_info:
        await handle_callback(
            resources, _oidc_providers(provider), Mock(spec=Users), state_token, "code", REDIRECT_URI
        )
    assert isinstance(exc_info.value.__cause__, OidcTokenError)


@pytest.mark.anyio
async def test_handle_callback_validates_the_id_token_against_the_stored_nonce_and_provider_client_id(
    oauth_client_factory, monkeypatch
):
    oidc_client = _oidc_client_mock()
    resources = ServerResources(oidc_client=oidc_client)
    provider = _provider()
    state_token = resources.oidc_start_pending_login(provider.issuer, "the-nonce", "v", "/admin")
    monkeypatch.setattr("Pytre_Server.oidc.link_user", Mock(return_value=Mock(spec=User)))

    await handle_callback(resources, _oidc_providers(provider), Mock(spec=Users), state_token, "code", REDIRECT_URI)

    oidc_client.validate_id_token.assert_called_once()
    _, kwargs = oidc_client.validate_id_token.call_args
    assert kwargs["client_id"] == provider.client_id
    assert kwargs["nonce"] == "the-nonce"


# ================================================================================
# handle_callback() - success path (link_user() itself is tested separately below)
# ================================================================================


@pytest.mark.anyio
async def test_handle_callback_returns_the_linked_user_and_the_stored_next_path(oauth_client_factory, monkeypatch):
    resources = _resources()
    provider = _provider()
    state_token = resources.oidc_start_pending_login(provider.issuer, "n", "v", "/admin/servers")
    fake_user = Mock(spec=User)
    link_user_mock = Mock(return_value=fake_user)
    monkeypatch.setattr("Pytre_Server.oidc.link_user", link_user_mock)
    users = Mock(spec=Users)

    user, next_path = await handle_callback(
        resources, _oidc_providers(provider), users, state_token, "code", REDIRECT_URI
    )

    assert user is fake_user
    assert next_path == "/admin/servers"
    link_user_mock.assert_called_once_with(users, provider, {"sub": "sub-123"}, "")


# ================================================================================
# link_user() - real KeePass database (round-trip behavior a mock would only echo)
# ================================================================================


@pytest.fixture
def kee(monkeypatch, tmp_path) -> Kee:
    db_copy = tmp_path / "Pytre_test.db"
    shutil.copy(TEST_DB_PATH, db_copy)
    monkeypatch.setattr(kee_module, "KEE_FILE", db_copy)
    return Kee(password=TEST_DB_PASSWORD)


@pytest.fixture
def users(kee) -> Users:
    return Users(kee)


def _oidc_user(users: Users, username: str = "sso_user", **overrides) -> User:
    user = User(users, detect_user=False)
    user.username = username
    user.auth_type = AuthType.OIDC
    for name, value in overrides.items():
        setattr(user, name, value)
    user.save()
    return user


def _reloaded_user(username: str) -> User:
    return User(Users(Kee(password=TEST_DB_PASSWORD)), username=username)


@pytest.mark.parametrize("claims", [{"sub": "sub-123"}, {"sub": "sub-123", "preferred_username": ""}])
def test_link_user_rejects_a_missing_or_blank_username_claim(users, claims):
    with pytest.raises(OidcLoginError, match="preferred_username"):
        link_user(users, _provider(), claims)


def test_link_user_rejects_an_unknown_username(users):
    with pytest.raises(OidcLoginError, match="No OIDC-enabled"):
        link_user(users, _provider(), {"sub": "sub-123", "preferred_username": "ghost"})


def test_link_user_rejects_a_non_oidc_user(users):
    user = User(users, detect_user=False)
    user.username = "basic_user"
    user.auth_type = AuthType.BASIC
    user.save()

    with pytest.raises(OidcLoginError, match="No OIDC-enabled"):
        link_user(users, _provider(), {"sub": "sub-123", "preferred_username": "basic_user"})


def test_link_user_links_the_subject_on_first_login(users):
    _oidc_user(users, "sso_user")

    linked = link_user(users, _provider(), {"sub": "sub-123", "preferred_username": "sso_user"})

    assert linked.oidc_link is not None
    assert linked.oidc_link.issuer == ISSUER
    assert linked.oidc_link.sub == "sub-123"
    reloaded = _reloaded_user("sso_user")
    assert reloaded.oidc_link is not None
    assert reloaded.oidc_link.sub == "sub-123"  # actually persisted, not just in memory


def test_link_user_captures_the_refresh_token_and_verification_time_on_first_login(users):
    _oidc_user(users, "sso_user")
    before = datetime.now(timezone.utc)

    linked = link_user(
        users, _provider(), {"sub": "sub-123", "preferred_username": "sso_user"}, refresh_token="rt-abc"
    )

    identity = linked.oidc_link
    assert identity is not None
    assert identity.refresh_token == "rt-abc"
    assert identity.last_verified is not None
    assert identity.last_verified >= before
    reloaded = _reloaded_user("sso_user").oidc_link
    assert reloaded is not None
    assert reloaded.refresh_token == "rt-abc"


def test_link_user_accepts_a_returning_user_whose_subject_already_matches(users):
    _oidc_user(users, "sso_user")
    link_user(users, _provider(), {"sub": "sub-123", "preferred_username": "sso_user"}, refresh_token="rt-old")

    linked = link_user(
        users, _provider(), {"sub": "sub-123", "preferred_username": "sso_user"}, refresh_token="rt-new"
    )

    assert linked.username == "sso_user"


def test_link_user_rotates_the_refresh_token_and_bumps_last_verified_on_every_successful_login(users):
    """Not just the first login - each return trip through an already-linked provider proves the
    account is still active, at no extra cost, so both fields refresh every time."""
    _oidc_user(users, "sso_user")
    first = link_user(users, _provider(), {"sub": "sub-123", "preferred_username": "sso_user"}, refresh_token="rt-old")
    assert first.oidc_link is not None
    first_verified_at = first.oidc_link.last_verified

    second = link_user(
        users, _provider(), {"sub": "sub-123", "preferred_username": "sso_user"}, refresh_token="rt-new"
    )

    identity = second.oidc_link
    assert identity is not None
    assert identity.refresh_token == "rt-new"
    assert identity.last_verified is not None
    assert first_verified_at is not None
    assert identity.last_verified >= first_verified_at


def test_link_user_rejects_a_mismatched_subject(users):
    _oidc_user(users, "sso_user")
    link_user(users, _provider(), {"sub": "original-sub", "preferred_username": "sso_user"})

    with pytest.raises(OidcLoginError, match="linked to a different"):
        link_user(users, _provider(), {"sub": "someone-elses-sub", "preferred_username": "sso_user"})

    # never overwritten by a rejected attempt
    reloaded = _reloaded_user("sso_user").oidc_link
    assert reloaded is not None
    assert reloaded.sub == "original-sub"


def test_link_user_uses_the_configured_username_claim(users):
    _oidc_user(users, "sso_user")

    linked = link_user(users, _provider(username_claim="email"), {"sub": "sub-123", "email": "sso_user"})

    assert linked.username == "sso_user"


def test_link_user_finds_a_returning_user_by_sub_even_when_the_username_claim_no_longer_matches(users):
    """The whole point of looking a returning user up by (issuer, sub) first: a Pytre-side rename,
    or the username_claim's value changing at the IdP, must not break a login that already proved
    its identity once - sub is the durable link, per link_user()'s own docstring.

    The rename goes through the same `users`/Kee instance link_user() will query next - a separate
    Kee instance (as _reloaded_user() builds) wouldn't see it without an explicit reload, which
    isn't what this test is about."""
    _oidc_user(users, "original_name")
    link_user(users, _provider(), {"sub": "sub-123", "preferred_username": "original_name"})

    # fresh object reflecting the link just saved - reusing the pre-link User instance here would
    # overwrite oidc_link with the None it still has in memory (User.save() rewrites every field
    # unconditionally, the same gotcha as Settings.save())
    user = User(users, username="original_name")
    user.username = "renamed_user"
    user.save()

    # the IdP still sends the old claim value - Pytre's own username moved on independently
    linked = link_user(users, _provider(), {"sub": "sub-123", "preferred_username": "original_name"})

    assert linked.username == "renamed_user"


def test_link_user_rejects_a_second_different_provider_once_already_linked(users):
    """Only one OidcLink exists per user now (the model was simplified from a per-issuer dict once
    the client's mediated-mode login settled on a single active provider) - a login via a
    different provider than the one already linked must be rejected, not silently switch the
    link over to it, same security posture as a mismatched sub on the same provider."""
    other_issuer = "https://other-idp.example.com"
    _oidc_user(users, "sso_user")
    link_user(users, _provider(), {"sub": "sub-a", "preferred_username": "sso_user"})

    with pytest.raises(OidcLoginError, match="linked to a different"):
        link_user(users, _provider(issuer=other_issuer), {"sub": "sub-b", "preferred_username": "sso_user"})

    reloaded = _reloaded_user("sso_user").oidc_link
    assert reloaded is not None
    assert reloaded.issuer == ISSUER
    assert reloaded.sub == "sub-a"


def test_link_user_logs_and_rejects_on_an_unknown_user(users, caplog):
    with caplog.at_level(logging.INFO):
        with pytest.raises(OidcLoginError):
            link_user(users, _provider(), {"sub": "sub-123", "preferred_username": "ghost"})

    assert "not an OIDC-enabled" in caplog.text


def test_link_user_logs_a_mismatched_subject_rejection(users, caplog):
    _oidc_user(users, "sso_user")
    link_user(users, _provider(), {"sub": "original-sub", "preferred_username": "sso_user"})

    with caplog.at_level(logging.INFO):
        with pytest.raises(OidcLoginError):
            link_user(users, _provider(), {"sub": "someone-elses-sub", "preferred_username": "sso_user"})

    assert "linked to a different identity provider account" in caplog.text


# ================================================================================
# verify_still_active() (Lot 6 - OIDC active account verification at /refresh)
# ================================================================================


@pytest.mark.anyio
async def test_verify_still_active_is_a_noop_when_last_verified_is_recent(users, oauth_client_factory):
    user = _oidc_user(
        users,
        oidc_link=OidcLink(
            issuer=ISSUER, sub="sub-1", refresh_token="rt",
            last_verified=datetime.now(timezone.utc) - timedelta(minutes=5),
        ),
    )

    result = await verify_still_active(_resources(), _oidc_providers(_provider()), user)

    assert result is False
    assert oauth_client_factory.instances == []


@pytest.mark.anyio
async def test_verify_still_active_calls_the_idp_when_overdue(users, oauth_client_factory):
    user = _oidc_user(
        users,
        oidc_link=OidcLink(
            issuer=ISSUER, sub="sub-1", refresh_token="rt",
            last_verified=datetime.now(timezone.utc) - timedelta(hours=2),
        ),
    )

    result = await verify_still_active(_resources(), _oidc_providers(_provider()), user)

    assert result is True
    assert len(oauth_client_factory.instances) == 1
    assert oauth_client_factory.instances[0].fetch_token_kwargs == {"refresh_token": "rt"}


@pytest.mark.anyio
async def test_verify_still_active_calls_the_idp_when_never_verified(users, oauth_client_factory):
    user = _oidc_user(users, oidc_link=OidcLink(issuer=ISSUER, sub="sub-1", refresh_token="rt", last_verified=None))

    result = await verify_still_active(_resources(), _oidc_providers(_provider()), user)

    assert result is True
    assert len(oauth_client_factory.instances) == 1


@pytest.mark.anyio
async def test_verify_still_active_rotates_refresh_token_and_bumps_last_verified_on_success(users, oauth_client_factory):
    original_last_verified = datetime.now(timezone.utc) - timedelta(hours=2)
    user = _oidc_user(
        users,
        oidc_link=OidcLink(issuer=ISSUER, sub="sub-1", refresh_token="old-rt", last_verified=original_last_verified),
    )
    oauth_client_factory.result = {"refresh_token": "rotated-rt"}

    result = await verify_still_active(_resources(), _oidc_providers(_provider()), user)

    assert result is True
    reloaded = _reloaded_user("sso_user").oidc_link
    assert reloaded is not None
    assert reloaded.refresh_token == "rotated-rt"
    assert reloaded.last_verified is not None
    assert reloaded.last_verified > original_last_verified


@pytest.mark.anyio
async def test_verify_still_active_keeps_old_refresh_token_if_idp_omits_one(users, oauth_client_factory):
    user = _oidc_user(
        users,
        oidc_link=OidcLink(
            issuer=ISSUER, sub="sub-1", refresh_token="old-rt",
            last_verified=datetime.now(timezone.utc) - timedelta(hours=2),
        ),
    )
    oauth_client_factory.result = {}  # no refresh_token key at all, mirrors a real IdP that didn't rotate it

    result = await verify_still_active(_resources(), _oidc_providers(_provider()), user)

    assert result is True
    reloaded = _reloaded_user("sso_user").oidc_link
    assert reloaded is not None
    assert reloaded.refresh_token == "old-rt"


@pytest.mark.anyio
async def test_verify_still_active_raises_on_invalid_grant_and_does_not_touch_the_link(users, oauth_client_factory):
    original_last_verified = datetime.now(timezone.utc) - timedelta(hours=2)
    user = _oidc_user(
        users,
        oidc_link=OidcLink(issuer=ISSUER, sub="sub-1", refresh_token="old-rt", last_verified=original_last_verified),
    )
    oauth_client_factory.exc = InvalidGrantError()

    with pytest.raises(OidcReverifyError):
        await verify_still_active(_resources(), _oidc_providers(_provider()), user)

    reloaded = _reloaded_user("sso_user").oidc_link
    assert reloaded is not None
    assert reloaded.refresh_token == "old-rt"
    assert reloaded.last_verified == original_last_verified


@pytest.mark.anyio
async def test_verify_still_active_tolerates_a_network_error_within_the_grace_window(users, oauth_client_factory):
    original_last_verified = datetime.now(timezone.utc) - timedelta(hours=2)
    user = _oidc_user(
        users,
        oidc_link=OidcLink(issuer=ISSUER, sub="sub-1", refresh_token="rt", last_verified=original_last_verified),
    )
    oauth_client_factory.exc = httpx2.ConnectTimeout("boom")

    result = await verify_still_active(_resources(), _oidc_providers(_provider()), user)  # must not raise

    assert result is False
    reloaded = _reloaded_user("sso_user").oidc_link
    assert reloaded is not None
    assert reloaded.last_verified == original_last_verified


@pytest.mark.anyio
async def test_verify_still_active_rejects_a_network_error_beyond_the_grace_window(users, oauth_client_factory):
    user = _oidc_user(
        users,
        oidc_link=OidcLink(
            issuer=ISSUER, sub="sub-1", refresh_token="rt",
            last_verified=datetime.now(timezone.utc) - timedelta(hours=25),
        ),
    )
    oauth_client_factory.exc = httpx2.ConnectTimeout("boom")

    with pytest.raises(OidcReverifyError):
        await verify_still_active(_resources(), _oidc_providers(_provider()), user)


@pytest.mark.anyio
async def test_verify_still_active_rejects_without_contacting_the_idp_when_no_refresh_token(users, oauth_client_factory):
    user = _oidc_user(users, oidc_link=OidcLink(issuer=ISSUER, sub="sub-1", refresh_token=""))

    with pytest.raises(OidcReverifyError):
        await verify_still_active(_resources(), _oidc_providers(_provider()), user)

    assert oauth_client_factory.instances == []


@pytest.mark.anyio
async def test_verify_still_active_rejects_without_contacting_the_idp_when_oidc_link_is_none(users, oauth_client_factory):
    user = _oidc_user(users)  # oidc_link stays None

    with pytest.raises(OidcReverifyError):
        await verify_still_active(_resources(), _oidc_providers(_provider()), user)

    assert oauth_client_factory.instances == []


@pytest.mark.anyio
async def test_verify_still_active_rejects_when_provider_is_missing(users, oauth_client_factory):
    user = _oidc_user(
        users,
        oidc_link=OidcLink(
            issuer=ISSUER, sub="sub-1", refresh_token="rt",
            last_verified=datetime.now(timezone.utc) - timedelta(hours=2),
        ),
    )

    with pytest.raises(OidcReverifyError):
        await verify_still_active(_resources(), _oidc_providers(), user)  # no provider configured at all

    assert oauth_client_factory.instances == []


@pytest.mark.anyio
async def test_verify_still_active_rejects_when_provider_is_disabled(users, oauth_client_factory):
    """Provisional per Pytre_Server.oidc.verify_still_active() - a disabled/removed provider is
    treated as a deliberate admin revocation, not a transient failure to tolerate."""
    user = _oidc_user(
        users,
        oidc_link=OidcLink(
            issuer=ISSUER, sub="sub-1", refresh_token="rt",
            last_verified=datetime.now(timezone.utc) - timedelta(hours=2),
        ),
    )

    with pytest.raises(OidcReverifyError):
        await verify_still_active(_resources(), _oidc_providers(_provider(enabled=False)), user)

    assert oauth_client_factory.instances == []


@pytest.mark.anyio
async def test_verify_still_active_tolerates_a_discovery_failure_within_the_grace_window(users):
    original_last_verified = datetime.now(timezone.utc) - timedelta(hours=2)
    user = _oidc_user(
        users,
        oidc_link=OidcLink(issuer=ISSUER, sub="sub-1", refresh_token="rt", last_verified=original_last_verified),
    )
    resources = _resources(discover_exc=OidcDiscoveryError("unreachable"))

    result = await verify_still_active(resources, _oidc_providers(_provider()), user)  # must not raise

    assert result is False
    reloaded = _reloaded_user("sso_user").oidc_link
    assert reloaded is not None
    assert reloaded.last_verified == original_last_verified


@pytest.mark.anyio
async def test_verify_still_active_rejects_a_discovery_failure_beyond_the_grace_window(users):
    user = _oidc_user(
        users,
        oidc_link=OidcLink(
            issuer=ISSUER, sub="sub-1", refresh_token="rt",
            last_verified=datetime.now(timezone.utc) - timedelta(hours=25),
        ),
    )
    resources = _resources(discover_exc=OidcDiscoveryError("unreachable"))

    with pytest.raises(OidcReverifyError):
        await verify_still_active(resources, _oidc_providers(_provider()), user)
