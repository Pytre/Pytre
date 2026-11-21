"""
Tests for Pytre_Core/remotes.py
"""

import json
import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx2
import jwt
import pytest

from Pytre_Core import remotes as remotes_module
from Pytre_Core.app_folders import get_config_folder
from Pytre_Core.policy import PlainHttpRemotesPolicy
from Pytre_Core.remote_protocol import JOB_ABANDON_SECS, JOB_WAIT_SECS, Batch
from Pytre_Core.remotes import (
    MANAGED_REMOTES_FILENAME,
    REMOTES_CIPHER,
    REMOTES_FILENAME,
    Remote,
    RemoteAddress,
    RemoteAuthError,
    RemoteError,
    RemoteExecutionError,
    RemoteHTTPStatusError,
    RemoteInsecureChannelError,
    RemoteJob,
    RemoteRequestError,
    RemoteServer,
    RemoteSsoRequiredError,
    RemoteStopped,
    Remotes,
    detail_from_response,
)
from Pytre_Core.users import AuthType, Users
from Pytre_Core.user_prefs import DisplayFormat


class _FakeResponse:
    """Minimal stand-in for httpx2.Response - only what Remote/RemoteServer actually use."""

    def __init__(self, json_data=None, status_code: int = 200, status_error: Exception | None = None):
        self._json_data = json_data
        self.status_code = status_code
        self._status_error = status_error

    def raise_for_status(self) -> None:
        if self._status_error is not None:
            raise self._status_error

    def json(self):
        return self._json_data


def _servers_payload(servers: list[dict], user_infos: dict | None = None) -> dict:
    """Builds the {"servers": [...], "user_infos": {...}} shape GET /servers actually returns
    (Remote.refresh_servers() expects this, not a bare list)."""
    return {"servers": servers, "user_infos": user_infos or {}}


def _make_token() -> str:
    """A well-formed JWT string, comfortably far from expiry (well outside
    _token_is_valid()'s near-expiry margin) - enough to make RemoteAddress._token_is_valid()
    treat it as present/well-formed/valid, regardless of its actual content (the client never
    verifies its own token's signature)."""
    payload = {"sub": "alice", "exp": datetime.now(timezone.utc) + timedelta(minutes=30)}
    return jwt.encode(payload, "test-secret-at-least-32-bytes-long", algorithm="HS256")


def _make_expired_token() -> str:
    """A well-formed but already-expired JWT - used to confirm _token_is_valid() actually
    checks expiry (regression: verify_signature=False silently disables verify_exp too,
    unless it's explicitly re-enabled)."""
    payload = {"sub": "alice", "exp": datetime.now(timezone.utc) - timedelta(minutes=5)}
    return jwt.encode(payload, "test-secret-at-least-32-bytes-long", algorithm="HS256")


def _make_soon_to_expire_token() -> str:
    """A well-formed JWT that hasn't technically expired yet, but expires within
    _token_is_valid()'s default 5-second margin - used to confirm the token is treated as
    invalid well before it actually expires, not just at the exact expiry instant."""
    payload = {"sub": "alice", "exp": datetime.now(timezone.utc) + timedelta(seconds=2)}
    return jwt.encode(payload, "test-secret-at-least-32-bytes-long", algorithm="HS256")


REAL_PLAIN_HTTP_ALLOWED = remotes_module.plain_http_allowed  # kept before the fixture below replaces it


@pytest.fixture(autouse=True)
def plain_http_policy_allowed(monkeypatch):
    """No test depends on a Pytre.policy.json lying next to the code, nor on the cached policy of another test: plain
    HTTP to remotes is allowed unless a test monkeypatches plain_http_allowed() to say otherwise."""
    monkeypatch.setattr("Pytre_Core.remotes.plain_http_allowed", lambda: True)


@pytest.fixture(autouse=True)
def install_folder(tmp_path_factory, monkeypatch) -> Any:
    """Folder of the remotes deployed by the administrator, isolated per test: no test reads a Pytre.remotes.json
    lying next to the code. Kept out of tmp_path, which tests may expect empty."""
    folder = tmp_path_factory.mktemp("install")
    monkeypatch.setattr("Pytre_Core.remotes.get_install_folder", lambda: folder)
    return folder


@pytest.fixture
def auto_login_ok(monkeypatch):
    """Makes any Remote.login() call succeed via a full /login round trip, without caring
    about the two-tier refresh/login mechanics themselves (covered separately near
    Remote.login()) - lets tests focus on what happens once a Remote is authenticated."""
    monkeypatch.setattr(httpx2, "post", lambda **kwargs: _FakeResponse({"refresh_token": "r1", "access_token": "a1"}))


@pytest.fixture
def address() -> RemoteAddress:
    """Loopback host so login()'s HTTPS-or-local gate doesn't get in the way of tests that
    aren't specifically about that gate - see the dedicated tests near Remote.login(). A saved
    password, as login() never calls /login without one."""
    return RemoteAddress(url="http://127.0.0.1", port=8000, username="alice", password="secret")


@pytest.fixture
def remotes_file(tmp_path) -> Any:
    """Path to a remotes json file, isolated per test - never touches the real
    ~/Pytre/Pytre_Remotes.json. Doesn't need to exist: Remotes/load_remotes() handle a
    missing file as an empty collection, same as a fresh install."""
    return tmp_path / "remotes.json"


def _write_remotes_file(path, addresses: list[RemoteAddress]) -> None:
    """Writes a remotes json file in the same shape Remotes.save_remotes() produces -
    keyed by uuid, uuid itself excluded from each value."""
    data = {a.uuid: {k: v for k, v in a.to_dict().items() if k != "uuid"} for a in addresses}
    path.write_text(json.dumps(data), encoding="utf-8")


def _read_remotes_file(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _comparable(address_dict: dict) -> dict:
    """`address_dict` (RemoteAddress.to_dict()) with its secrets decrypted: each encryption gives a different text"""
    decrypted = {key: REMOTES_CIPHER.decrypt(address_dict[key]) for key in ("encrypted_pwd", "token")}
    return address_dict | decrypted


@pytest.fixture
def remotes(remotes_file) -> Remotes:
    """Empty Remotes collection - only used as the required back-reference for Remote(),
    not exercised directly by tests that don't need real persistence."""
    return Remotes(remotes_file)


# ================================================================================
# RemoteAddress
# ================================================================================


@pytest.mark.unit
def test_remote_address_from_dict_builds_valid_object():
    remote = RemoteAddress.from_dict({"url": "http://example.com", "port": 8000, "username": "alice"})

    assert remote.url == "http://example.com"
    assert remote.port == 8000
    assert remote.username == "alice"


@pytest.mark.unit
def test_remote_address_from_dict_coerces_string_port_to_int():
    remote = RemoteAddress.from_dict({"url": "http://example.com", "port": "8000", "username": "alice"})

    assert remote.port == 8000
    assert isinstance(remote.port, int)


@pytest.mark.unit
def test_remote_address_from_dict_rejects_non_dict():
    with pytest.raises(TypeError):
        RemoteAddress.from_dict("not_a_dict")  # type: ignore


@pytest.mark.unit
def test_remote_address_from_dict_rejects_missing_url():
    with pytest.raises(ValueError, match="required"):
        RemoteAddress.from_dict({"port": 8000})


@pytest.mark.unit
def test_remote_address_from_dict_rejects_missing_port():
    with pytest.raises(ValueError, match="required"):
        RemoteAddress.from_dict({"url": "http://example.com"})


@pytest.mark.unit
def test_remote_address_from_dict_rejects_empty_url():
    with pytest.raises(ValueError, match="empty"):
        RemoteAddress.from_dict({"url": "", "port": 8000})


@pytest.mark.unit
def test_remote_address_from_dict_rejects_non_numeric_port():
    with pytest.raises(ValueError, match="integer"):
        RemoteAddress.from_dict({"url": "http://example.com", "port": "not_a_number"})


@pytest.mark.unit
def test_remote_address_from_dict_rejects_missing_username():
    with pytest.raises(ValueError, match="required"):
        RemoteAddress.from_dict({"url": "http://example.com", "port": 8000})


@pytest.mark.unit
def test_remote_address_from_dict_rejects_empty_username():
    with pytest.raises(ValueError, match="empty"):
        RemoteAddress.from_dict({"url": "http://example.com", "port": 8000, "username": ""})


@pytest.mark.unit
def test_remote_address_to_dict_round_trips():
    original = {
        "uuid": "550e8400-e29b-41d4-a716-446655440000",
        "url": "http://example.com",
        "port": 8000,
        "username": "alice",
        "description": "Prod",
    }

    remote = RemoteAddress.from_dict(original)

    assert remote.to_dict() == original | {
        "encrypted_pwd": "",
        "token": "",
        "allow_unsecure_channel": False,
        "auth_method": "basic",
        "managed": False,
        "ask_for_login": True,
    }


@pytest.mark.unit
def test_remote_address_generates_uuid_when_not_given():
    remote = RemoteAddress(url="http://a", port=8000, username="alice")

    assert remote.uuid


@pytest.mark.unit
def test_remote_address_from_dict_generates_uuid_when_missing():
    remote = RemoteAddress.from_dict({"url": "http://a", "port": 8000, "username": "alice"})

    assert remote.uuid


@pytest.mark.unit
def test_remote_address_from_dict_preserves_given_uuid():
    """Regression: save()/load() rely on the uuid surviving a to_dict()/from_dict() round
    trip unchanged - a missing or regenerated uuid breaks identity across a reload."""
    remote = RemoteAddress.from_dict(
        {"uuid": "550e8400-e29b-41d4-a716-446655440000", "url": "http://a", "port": 8000, "username": "alice"}
    )

    assert remote.uuid == "550e8400-e29b-41d4-a716-446655440000"


@pytest.mark.unit
def test_remote_address_from_dict_leaves_description_unset_when_missing():
    """No default is synthesized and persisted at load time - a stale "url:port" baked in
    at creation wouldn't track later address edits. description_for_ui() computes the
    fallback dynamically instead (see test_description_for_ui_falls_back_to_id below)."""
    remote = RemoteAddress.from_dict({"url": "http://example.com", "port": 8000, "username": "alice"})

    assert not remote.description


@pytest.mark.unit
def test_description_for_ui_falls_back_to_id_when_description_is_empty():
    remote = RemoteAddress(url="http://example.com", port=8000, username="alice")

    assert remote.description_for_ui == remote.id


@pytest.mark.unit
def test_description_for_ui_returns_description_when_set():
    remote = RemoteAddress(url="http://example.com", port=8000, username="alice", description="Prod")

    assert remote.description_for_ui == "Prod"


@pytest.mark.unit
def test_remote_address_from_dict_keeps_provided_description():
    remote = RemoteAddress.from_dict(
        {"url": "http://example.com", "port": 8000, "username": "alice", "description": "Prod"}
    )

    assert remote.description == "Prod"


@pytest.mark.unit
def test_remote_address_allow_unsecure_channel_defaults_to_false():
    remote = RemoteAddress(url="http://a", port=8000, username="alice")

    assert remote.allow_unsecure_channel is False


@pytest.mark.unit
def test_remote_address_from_dict_defaults_allow_unsecure_channel_to_false():
    remote = RemoteAddress.from_dict({"url": "http://a", "port": 8000, "username": "alice"})

    assert remote.allow_unsecure_channel is False


@pytest.mark.unit
@pytest.mark.parametrize("value", [True, False])
def test_remote_address_from_dict_accepts_native_bool_allow_unsecure_channel(value):
    """The value read from Pytre_Remotes.json (via json.load()) is already a real bool -
    must be preserved as-is, not just treated as truthy/falsy."""
    remote = RemoteAddress.from_dict(
        {"url": "http://a", "port": 8000, "username": "alice", "allow_unsecure_channel": value}
    )

    assert remote.allow_unsecure_channel is value


@pytest.mark.unit
@pytest.mark.parametrize(
    "value, expected",
    [("true", True), ("True", True), ("TRUE", True), ("false", False), ("False", False), ("0", False), ("1", False)],
)
def test_remote_address_from_dict_parses_string_allow_unsecure_channel_case_insensitively(value, expected):
    """Regression: a csv-imported value is always a string - a naive truthy check would treat
    the string "false" (or "0") as truthy, same class of bug already hit with
    ServerField.TRUST_CERTIFICATE - only the case-insensitive text "true" must parse as True."""
    remote = RemoteAddress.from_dict(
        {"url": "http://a", "port": 8000, "username": "alice", "allow_unsecure_channel": value}
    )

    assert remote.allow_unsecure_channel is expected


@pytest.mark.unit
def test_remote_address_to_dict_includes_allow_unsecure_channel():
    remote = RemoteAddress(url="http://a", port=8000, username="alice", allow_unsecure_channel=True)

    assert remote.to_dict()["allow_unsecure_channel"] is True


@pytest.mark.unit
def test_remote_address_auth_method_defaults_to_basic():
    remote = RemoteAddress(url="http://a", port=8000, username="alice")

    assert remote.auth_method is AuthType.BASIC


@pytest.mark.unit
def test_remote_address_from_dict_defaults_auth_method_to_basic_when_missing():
    """An existing Pytre_Remotes.json predating this field has no auth_method key at all -
    must behave exactly as before (password login), not raise or silently break."""
    remote = RemoteAddress.from_dict({"url": "http://a", "port": 8000, "username": "alice"})

    assert remote.auth_method is AuthType.BASIC


@pytest.mark.unit
@pytest.mark.parametrize("value", ["basic", "BASIC", "oidc", "OIDC"])
def test_remote_address_from_dict_parses_auth_method_case_insensitively(value):
    remote = RemoteAddress.from_dict({"url": "http://a", "port": 8000, "username": "alice", "auth_method": value})

    assert remote.auth_method is AuthType.from_value(value)


@pytest.mark.unit
@pytest.mark.parametrize("value", ["none", "NONE", "not-a-real-auth-method", ""])
def test_remote_address_from_dict_falls_back_to_basic_for_none_or_invalid_auth_method(value):
    """AuthType.NONE has no meaning for a remote (there's no way to log in without a method) -
    treated the same as any other invalid/unrecognized value, falls back to AuthType.BASIC."""
    remote = RemoteAddress.from_dict({"url": "http://a", "port": 8000, "username": "alice", "auth_method": value})

    assert remote.auth_method is AuthType.BASIC


@pytest.mark.unit
def test_remote_address_to_dict_includes_auth_method():
    remote = RemoteAddress(url="http://a", port=8000, username="alice", auth_method=AuthType.OIDC)

    assert remote.to_dict()["auth_method"] == "oidc"


@pytest.mark.unit
def test_remote_address_from_dict_auth_method_round_trips():
    original = RemoteAddress(url="http://a", port=8000, username="alice", auth_method=AuthType.OIDC)

    rebuilt = RemoteAddress.from_dict(original.to_dict())

    assert rebuilt.auth_method is AuthType.OIDC


@pytest.mark.unit
def test_remote_address_id_is_built_from_username_url_and_port():
    remote = RemoteAddress(url="HTTP://Example.COM", port=8000, username="Alice")

    assert remote.id == "alice@http://example.com:8000"
    assert remote.id == remote.id  # stable across repeated access


@pytest.mark.unit
@pytest.mark.parametrize(
    "url, port, username",
    [("", 8000, "alice"), ("http://a", "", "alice"), ("http://a", 8000, ""), ("http://a", 8000, "  ")],
)
def test_remote_address_id_raises_if_any_value_blank(url, port, username):
    with pytest.raises(ValueError):
        RemoteAddress(url=url, port=port, username=username).id


@pytest.mark.unit
def test_remote_address_tokens_default_to_empty():
    remote = RemoteAddress(url="http://a", port=8000, username="alice")

    assert remote.access_token == ""
    assert remote.refresh_token == ""


@pytest.mark.unit
def test_remote_address_access_token_is_excluded_from_to_dict():
    """access_token is in-memory only, deliberately never persisted to Pytre_Remotes.json -
    only the refresh_token (as encrypted "token") is."""
    remote = RemoteAddress(url="http://a", port=8000, username="alice")
    remote.access_token = "some-access-token"

    assert "access_token" not in remote.to_dict()


@pytest.mark.unit
def test_remote_address_to_dict_encrypts_refresh_token():
    remote = RemoteAddress(url="http://a", port=8000, username="alice")
    remote.refresh_token = "some-refresh-token"

    assert remote.to_dict()["token"] != "some-refresh-token"
    assert remote.to_dict()["token"] != ""


@pytest.mark.unit
def test_remote_address_from_dict_never_sets_access_token():
    remote = RemoteAddress.from_dict({"url": "http://a", "port": 8000, "username": "alice"})

    assert remote.access_token == ""


@pytest.mark.unit
def test_remote_address_from_dict_decrypts_refresh_token_round_trip():
    original = RemoteAddress(url="http://a", port=8000, username="alice")
    original.refresh_token = "some-refresh-token"

    rebuilt = RemoteAddress.from_dict(original.to_dict())

    assert rebuilt.refresh_token == "some-refresh-token"


# ================================================================================
# RemoteAddress.refresh_token_is_valid / access_token_is_valid
# ================================================================================


@pytest.mark.unit
def test_refresh_token_is_valid_false_when_empty():
    remote = RemoteAddress(url="http://a", port=8000, username="alice")

    assert remote.refresh_token_is_valid is False


@pytest.mark.unit
def test_refresh_token_is_valid_false_when_malformed():
    remote = RemoteAddress(url="http://a", port=8000, username="alice")
    remote.refresh_token = "not-a-real-token"

    assert remote.refresh_token_is_valid is False


@pytest.mark.unit
def test_refresh_token_is_valid_true_for_a_well_formed_unexpired_token():
    remote = RemoteAddress(url="http://a", port=8000, username="alice")
    remote.refresh_token = _make_token()

    assert remote.refresh_token_is_valid is True


@pytest.mark.unit
def test_refresh_token_is_valid_false_when_expired():
    """Regression: jwt.decode(token, options={"verify_signature": False}) silently disables
    expiry checking too unless verify_exp is explicitly re-enabled - a token past its exp
    claim used to be reported as valid."""
    remote = RemoteAddress(url="http://a", port=8000, username="alice")
    remote.refresh_token = _make_expired_token()

    assert remote.refresh_token_is_valid is False


@pytest.mark.unit
def test_refresh_token_is_valid_false_when_expiring_within_margin():
    """A token that hasn't technically hit its exp claim yet, but expires soon enough that it
    might not survive the round trip to the server, must still be treated as unusable - not
    just tokens that are already fully expired."""
    remote = RemoteAddress(url="http://a", port=8000, username="alice")
    remote.refresh_token = _make_soon_to_expire_token()

    assert remote.refresh_token_is_valid is False


@pytest.mark.unit
def test_access_token_is_valid_false_when_empty():
    remote = RemoteAddress(url="http://a", port=8000, username="alice")

    assert remote.access_token_is_valid is False


@pytest.mark.unit
def test_access_token_is_valid_true_for_a_well_formed_unexpired_token():
    remote = RemoteAddress(url="http://a", port=8000, username="alice")
    remote.access_token = _make_token()

    assert remote.access_token_is_valid is True


@pytest.mark.unit
def test_access_token_is_valid_false_when_expired():
    remote = RemoteAddress(url="http://a", port=8000, username="alice")
    remote.access_token = _make_expired_token()

    assert remote.access_token_is_valid is False


@pytest.mark.unit
def test_access_token_is_valid_false_when_expiring_within_margin():
    remote = RemoteAddress(url="http://a", port=8000, username="alice")
    remote.access_token = _make_soon_to_expire_token()

    assert remote.access_token_is_valid is False


# ================================================================================
# detail_from_response
# ================================================================================


@pytest.mark.unit
def test_detail_from_response_returns_detail_when_present():
    response = _FakeResponse(json_data={"detail": "Something specific went wrong"})

    assert detail_from_response(response, "fallback") == "Something specific went wrong"


@pytest.mark.unit
def test_detail_from_response_falls_back_when_response_is_none():
    assert detail_from_response(None, "fallback") == "fallback"


@pytest.mark.unit
def test_detail_from_response_falls_back_when_detail_is_not_a_string():
    """FastAPI's own validation errors (422) put a list of error objects in "detail", not a
    plain string - must not be used as-is."""
    response = _FakeResponse(json_data={"detail": [{"loc": ["body"], "msg": "field required"}]})

    assert detail_from_response(response, "fallback") == "fallback"


@pytest.mark.unit
def test_detail_from_response_falls_back_when_detail_is_empty():
    response = _FakeResponse(json_data={"detail": ""})

    assert detail_from_response(response, "fallback") == "fallback"


@pytest.mark.unit
def test_detail_from_response_falls_back_when_body_has_no_detail_key():
    response = _FakeResponse(json_data={"other_key": "value"})

    assert detail_from_response(response, "fallback") == "fallback"


@pytest.mark.unit
def test_detail_from_response_falls_back_when_body_is_not_a_json_object():
    """Valid JSON but not an object (e.g. the body is literally `null`) - `.get("detail")`
    would raise AttributeError, not silently return None."""
    response = _FakeResponse(json_data=None)

    assert detail_from_response(response, "fallback") == "fallback"


@pytest.mark.unit
def test_detail_from_response_falls_back_when_body_is_not_json():
    class _NotJsonResponse:
        def json(self):
            raise ValueError("not json")

    assert detail_from_response(_NotJsonResponse(), "fallback") == "fallback"


# ================================================================================
# Remote.check_health
# ================================================================================


@pytest.mark.unit
def test_check_health_returns_true_on_success(address, remotes, monkeypatch):
    monkeypatch.setattr(httpx2, "get", lambda **kwargs: _FakeResponse({"status": "ok"}))

    result = Remote(remotes, address).check_health()

    assert result is True


@pytest.mark.unit
def test_check_health_sends_correct_url(address, remotes, monkeypatch):
    captured = {}

    def fake_get(url, timeout=None):
        captured["url"] = url
        return _FakeResponse({"status": "ok"})

    monkeypatch.setattr(httpx2, "get", fake_get)

    Remote(remotes, address).check_health()

    assert captured["url"] == "http://127.0.0.1:8000/health"


@pytest.mark.unit
def test_check_health_success_clears_last_error(address, remotes, monkeypatch):
    remote = Remote(remotes, address)
    remote.last_error = RemoteError("previous failure")
    monkeypatch.setattr(httpx2, "get", lambda **kwargs: _FakeResponse({"status": "ok"}))

    remote.check_health()

    assert remote.last_error is None


@pytest.mark.unit
def test_check_health_raises_remote_request_error_on_connection_failure(address, remotes, monkeypatch):
    def fake_get(**kwargs):
        raise httpx2.ConnectError("boom")

    monkeypatch.setattr(httpx2, "get", fake_get)

    remote = Remote(remotes, address)
    with pytest.raises(RemoteRequestError):
        remote.check_health()

    assert isinstance(remote.last_error, RemoteRequestError)


@pytest.mark.unit
def test_check_health_raises_remote_http_status_error_with_detail(address, remotes, monkeypatch):
    fake_response = _FakeResponse(json_data={"detail": "Server is shutting down"})
    error = httpx2.HTTPStatusError("503", request=None, response=fake_response)  # type: ignore
    monkeypatch.setattr(httpx2, "get", lambda **kwargs: _FakeResponse(status_error=error))

    remote = Remote(remotes, address)
    with pytest.raises(RemoteHTTPStatusError, match="Server is shutting down"):
        remote.check_health()

    assert isinstance(remote.last_error, RemoteHTTPStatusError)


# ================================================================================
# Remote.login
# ================================================================================


@pytest.mark.unit
def test_login_success_stores_both_tokens(address, remotes, monkeypatch):
    """No refresh_token stored yet, so login() must go straight to a full /login and store
    both the new refresh_token and access_token it returns."""
    monkeypatch.setattr(httpx2, "post", lambda **kwargs: _FakeResponse({"refresh_token": "r1", "access_token": "a1"}))

    result = Remote(remotes, address).login()

    assert result is True
    assert address.refresh_token == "r1"
    assert address.access_token == "a1"


@pytest.mark.unit
def test_login_sends_url_username_and_password(address, remotes, monkeypatch):
    address.password = "s3cret"
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse({"refresh_token": "r1", "access_token": "a1"})

    monkeypatch.setattr(httpx2, "post", fake_post)

    Remote(remotes, address).login()

    assert captured["url"] == "http://127.0.0.1:8000/login"
    assert captured["json"] == {"username": "alice", "password": "s3cret"}


@pytest.mark.unit
def test_login_uses_valid_refresh_token_and_skips_full_login(address, remotes, monkeypatch):
    """The cheap path: a still-valid refresh_token gets a fresh access_token via /refresh,
    without ever sending the username/password again."""
    address.refresh_token = _make_token()
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse({"access_token": "new-access"})

    monkeypatch.setattr(httpx2, "post", fake_post)

    result = Remote(remotes, address).login()

    assert result is True
    assert captured["url"] == "http://127.0.0.1:8000/refresh"
    assert captured["json"] == {"refresh_token": address.refresh_token}
    assert address.access_token == "new-access"


@pytest.mark.unit
def test_login_via_refresh_persists_a_new_refresh_token_when_the_server_sends_one(address, remotes, monkeypatch):
    """Lot 7: an OIDC account just reconfirmed active at the IdP gets a fresh, longer-lived Pytre
    refresh token back from /refresh - persisted immediately, or it would only live in memory for
    this session and be lost on the next Pytre_Client restart."""
    address.refresh_token = _make_token()
    monkeypatch.setattr(
        httpx2, "post", lambda **kwargs: _FakeResponse({"access_token": "new-access", "refresh_token": "rotated-rt"})
    )

    result = Remote(remotes, address).login()

    assert result is True
    assert address.refresh_token == "rotated-rt"
    saved = _read_remotes_file(remotes.remotes_file)
    assert REMOTES_CIPHER.decrypt(saved[address.uuid]["token"]) == "rotated-rt"


@pytest.mark.unit
def test_login_via_refresh_does_not_persist_when_the_server_omits_a_new_refresh_token(address, remotes, monkeypatch):
    """The common case (most /refresh calls don't reverify against the IdP, and a BASIC account
    never gets a rolled refresh token at all) must behave exactly as before Lot 7 - nothing written
    to disk just because /refresh was called."""
    address.refresh_token = _make_token()
    monkeypatch.setattr(httpx2, "post", lambda **kwargs: _FakeResponse({"access_token": "new-access"}))

    result = Remote(remotes, address).login()

    assert result is True
    assert not remotes.remotes_file.exists()  # save() never called, nothing persisted


@pytest.mark.unit
def test_login_via_refresh_does_not_raise_for_a_standalone_remote_without_a_remotes_collection(address, monkeypatch):
    """A Remote built only to carry an address (e.g. RemoteServer.deserialize(), used for the
    execution round-trip) has no Remotes collection to persist to - Remote.save() would raise
    RuntimeError there, so persistence must be skipped silently rather than attempted."""
    address.refresh_token = _make_token()
    monkeypatch.setattr(
        httpx2, "post", lambda **kwargs: _FakeResponse({"access_token": "new-access", "refresh_token": "rotated-rt"})
    )

    result = Remote(None, address).login()  # must not raise

    assert result is True
    assert address.refresh_token == "rotated-rt"


@pytest.mark.unit
def test_login_via_full_login_persists_the_new_token_pair(address, remotes, monkeypatch):
    """The full username/password /login path now persists too, closing the same general gap -
    not OIDC-specific, but needed for the OIDC-specific rolling renewal above to actually survive
    a Pytre_Client restart."""
    monkeypatch.setattr(httpx2, "post", lambda **kwargs: _FakeResponse({"refresh_token": "r1", "access_token": "a1"}))

    result = Remote(remotes, address).login()

    assert result is True
    saved = _read_remotes_file(remotes.remotes_file)
    assert REMOTES_CIPHER.decrypt(saved[address.uuid]["token"]) == "r1"


@pytest.mark.unit
def test_login_via_full_login_does_not_raise_for_a_standalone_remote_without_a_remotes_collection(
    address, monkeypatch
):
    monkeypatch.setattr(httpx2, "post", lambda **kwargs: _FakeResponse({"refresh_token": "r1", "access_token": "a1"}))

    result = Remote(None, address).login()  # must not raise

    assert result is True
    assert address.refresh_token == "r1"


@pytest.mark.unit
def test_login_skips_refresh_call_when_refresh_token_is_expired(address, remotes, monkeypatch):
    """Regression: an expired refresh_token used to be reported as valid (see
    test_refresh_token_is_valid_false_when_expired) - login() would then waste a POST /refresh
    round trip guaranteed to fail server-side before falling back to a full /login. It must
    now go straight to /login."""
    address.refresh_token = _make_expired_token()
    address.password = "s3cret"
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        return _FakeResponse({"refresh_token": "new-refresh", "access_token": "new-access"})

    monkeypatch.setattr(httpx2, "post", fake_post)

    result = Remote(remotes, address).login()

    assert result is True
    assert captured["url"] == "http://127.0.0.1:8000/login"  # never called /refresh


@pytest.mark.unit
def test_login_falls_back_to_full_login_when_refresh_request_fails(address, remotes, monkeypatch):
    """Regression: a valid-looking but server-rejected refresh_token must fall through to a
    full username/password /login, not give up immediately (an early `return False` here was
    a real bug found in review - it skipped this fallback entirely)."""
    address.refresh_token = _make_token()
    address.password = "s3cret"

    def fake_post(url, json, timeout):
        if url.endswith("/refresh"):
            raise httpx2.ConnectError("boom")
        return _FakeResponse({"refresh_token": "new-refresh", "access_token": "new-access"})

    monkeypatch.setattr(httpx2, "post", fake_post)

    result = Remote(remotes, address).login()

    assert result is True
    assert address.refresh_token == "new-refresh"
    assert address.access_token == "new-access"


@pytest.mark.unit
def test_login_raises_remote_request_error_and_clears_tokens_on_request_error(address, remotes, monkeypatch):
    address.access_token = "stale-access-token"

    def fake_post(**kwargs):
        raise httpx2.ConnectError("boom")

    monkeypatch.setattr(httpx2, "post", fake_post)

    remote = Remote(remotes, address)
    with pytest.raises(RemoteRequestError):
        remote.login()

    assert address.access_token == ""
    assert address.refresh_token == ""
    assert isinstance(remote.last_error, RemoteRequestError)


@pytest.mark.unit
def test_login_raises_remote_auth_error_and_clears_tokens_on_http_status_error(address, remotes, monkeypatch):
    """A HTTPStatusError on /login is always a credentials problem - the API has no other
    reason to reject a login attempt with a non-2xx status."""
    address.access_token = "stale-access-token"
    error = httpx2.HTTPStatusError("401", request=None, response=None)  # type: ignore
    monkeypatch.setattr(httpx2, "post", lambda **kwargs: _FakeResponse(status_error=error))

    remote = Remote(remotes, address)
    with pytest.raises(RemoteAuthError):
        remote.login()

    assert address.access_token == ""
    assert address.refresh_token == ""
    assert isinstance(remote.last_error, RemoteAuthError)


@pytest.mark.unit
def test_login_refuses_plain_http_to_a_non_local_remote(address, remotes, monkeypatch):
    """Regression: sending the password over plain HTTP to a real, non-local host would
    expose it in cleartext on the network - login() must refuse before ever attempting the
    request, not just log a warning after the fact."""
    address.url = "http://example.com"

    def fake_post(**kwargs):
        pytest.fail("httpx2.post should not be called")

    monkeypatch.setattr(httpx2, "post", fake_post)

    remote = Remote(remotes, address)
    with pytest.raises(RemoteInsecureChannelError):
        remote.login()

    assert isinstance(remote.last_error, RemoteInsecureChannelError)


@pytest.mark.unit
def test_login_allows_https_to_a_non_local_remote(address, remotes, monkeypatch):
    address.url = "https://example.com"
    monkeypatch.setattr(httpx2, "post", lambda **kwargs: _FakeResponse({"refresh_token": "r1", "access_token": "a1"}))

    result = Remote(remotes, address).login()

    assert result is True
    assert address.refresh_token == "r1"
    assert address.access_token == "a1"


@pytest.mark.unit
def test_login_allows_plain_http_to_localhost(address, remotes, monkeypatch):
    address.url = "http://localhost"
    monkeypatch.setattr(httpx2, "post", lambda **kwargs: _FakeResponse({"refresh_token": "r1", "access_token": "a1"}))

    result = Remote(remotes, address).login()

    assert result is True


@pytest.mark.unit
def test_login_allows_plain_http_to_a_private_network_address(address, remotes, monkeypatch):
    """Not just localhost/127.0.0.1 - any RFC 1918 private-network address is exempt, since
    Pytre_Server is expected to run on an internal network, not just on the same machine."""
    address.url = "http://192.168.1.10"
    monkeypatch.setattr(httpx2, "post", lambda **kwargs: _FakeResponse({"refresh_token": "r1", "access_token": "a1"}))

    result = Remote(remotes, address).login()

    assert result is True


@pytest.mark.unit
def test_login_refuses_plain_http_to_a_public_ip(address, remotes, monkeypatch):
    address.url = "http://8.8.8.8"

    def fake_post(**kwargs):
        pytest.fail("httpx2.post should not be called")

    monkeypatch.setattr(httpx2, "post", fake_post)

    remote = Remote(remotes, address)
    with pytest.raises(RemoteInsecureChannelError):
        remote.login()


@pytest.mark.unit
def test_login_allows_plain_http_to_a_public_ip_when_allow_unsecure_channel_is_true(address, remotes, monkeypatch):
    """The explicit opt-out (e.g. an enterprise/Azure network not detected as private) lets a plain HTTP login
    through when the enterprise policy allows plain HTTP - login() proceeds normally."""
    address.url = "http://8.8.8.8"
    address.allow_unsecure_channel = True
    monkeypatch.setattr(httpx2, "post", lambda **kwargs: _FakeResponse({"refresh_token": "r1", "access_token": "a1"}))

    result = Remote(remotes, address).login()

    assert result is True


# ================================================================================
# Plain HTTP and the enterprise policy (plain_http_remotes)
# ================================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    ("url", "allow_unsecure", "plain_http_allowed", "expected"),
    [
        ("https://srv.example.com", False, False, True),
        ("http://127.0.0.1", False, False, True),
        ("http://127.1.2.3", False, False, True),
        ("http://localhost", False, False, True),
        ("http://[::1]", False, False, True),
        ("http://10.0.0.5", False, True, True),
        ("http://10.0.0.5", False, False, False),
        ("http://192.168.1.10", True, False, False),
        ("http://srv.example.com", True, True, True),
        ("http://srv.example.com", True, False, False),
        ("http://srv.example.com", False, True, False),
        ("http://8.8.8.8", False, True, False),
    ],
    ids=[
        "https",
        "loopback",
        "loopback-range",
        "localhost",
        "ipv6-loopback",
        "private-allowed",
        "private-disabled",
        "private-ticked-disabled",
        "host-ticked-allowed",
        "host-ticked-disabled",
        "host-unticked-allowed",
        "public-allowed",
    ],
)
def test_channel_is_allowed(url, allow_unsecure, plain_http_allowed, expected):
    address = RemoteAddress(url=url, port=8000, username="alice", allow_unsecure_channel=allow_unsecure)

    assert address.channel_is_allowed(plain_http_allowed) is expected


@pytest.mark.unit
def test_check_channel_accepts_an_allowed_channel(address, remotes):
    remote = Remote(remotes, address)  # http://127.0.0.1

    remote.check_channel()

    assert remote.last_error is None


@pytest.mark.unit
def test_check_channel_refusal_names_the_organization_when_the_policy_disables_plain_http(
    address, remotes, monkeypatch
):
    monkeypatch.setattr("Pytre_Core.remotes.plain_http_allowed", lambda: False)
    address.url = "http://10.0.0.5"
    remote = Remote(remotes, address)

    expected = "disabled by your organization: its address must start with https://"
    with pytest.raises(RemoteInsecureChannelError, match=expected):
        remote.check_channel()

    assert isinstance(remote.last_error, RemoteInsecureChannelError)


@pytest.mark.unit
def test_check_channel_refusal_when_the_policy_allows_plain_http(address, remotes):
    address.url = "http://8.8.8.8"

    with pytest.raises(RemoteInsecureChannelError, match="not allowed over plain HTTP"):
        Remote(remotes, address).check_channel()


@pytest.mark.unit
def test_login_ignores_allow_unsecure_channel_when_the_policy_disables_plain_http(address, remotes, monkeypatch):
    """With plain_http_remotes disabled, neither a private address nor the user's opt-out lets credentials through."""
    monkeypatch.setattr("Pytre_Core.remotes.plain_http_allowed", lambda: False)
    address.url = "http://192.168.1.10"
    address.allow_unsecure_channel = True

    def fake_post(**kwargs):
        pytest.fail("httpx2.post should not be called")

    monkeypatch.setattr(httpx2, "post", fake_post)

    with pytest.raises(RemoteInsecureChannelError):
        Remote(remotes, address).login()


@pytest.mark.unit
def test_login_still_allows_plain_http_to_this_machine_when_the_policy_disables_it(address, remotes, monkeypatch):
    monkeypatch.setattr("Pytre_Core.remotes.plain_http_allowed", lambda: False)
    monkeypatch.setattr(httpx2, "post", lambda **kwargs: _FakeResponse({"refresh_token": "r1", "access_token": "a1"}))

    assert Remote(remotes, address).login() is True  # http://127.0.0.1


@pytest.mark.unit
@pytest.mark.parametrize(("policy", "expected"), [("ALLOWED", True), ("DISABLED", False)])
def test_plain_http_allowed_follows_the_policy(monkeypatch, policy, expected):
    monkeypatch.setattr(PlainHttpRemotesPolicy, "load", classmethod(lambda cls: PlainHttpRemotesPolicy[policy]))
    REAL_PLAIN_HTTP_ALLOWED.cache_clear()
    try:
        assert REAL_PLAIN_HTTP_ALLOWED() is expected
    finally:
        REAL_PLAIN_HTTP_ALLOWED.cache_clear()


@pytest.mark.unit
def test_plain_http_allowed_reads_the_policy_once(monkeypatch):
    """The policy file doesn't change while the app runs: reading it at each login would only flood the logs."""
    loads: list[int] = []

    def fake_load(cls):
        loads.append(1)
        return PlainHttpRemotesPolicy.ALLOWED

    monkeypatch.setattr(PlainHttpRemotesPolicy, "load", classmethod(fake_load))
    REAL_PLAIN_HTTP_ALLOWED.cache_clear()
    try:
        REAL_PLAIN_HTTP_ALLOWED()
        REAL_PLAIN_HTTP_ALLOWED()
    finally:
        REAL_PLAIN_HTTP_ALLOWED.cache_clear()

    assert len(loads) == 1


# ================================================================================
# Remote.login - SSO (AuthType.OIDC remotes have no password to fall back on)
# ================================================================================


@pytest.mark.unit
def test_login_raises_sso_required_for_an_oidc_remote_with_no_refresh_token(address, remotes, monkeypatch):
    address.auth_method = AuthType.OIDC

    def fake_post(**kwargs):
        pytest.fail("httpx2.post should not be called - there's no password to send")

    monkeypatch.setattr(httpx2, "post", fake_post)

    remote = Remote(remotes, address)
    with pytest.raises(RemoteSsoRequiredError):
        remote.login()

    assert isinstance(remote.last_error, RemoteSsoRequiredError)


@pytest.mark.unit
def test_login_raises_sso_required_for_an_oidc_remote_with_an_expired_refresh_token(address, remotes, monkeypatch):
    address.auth_method = AuthType.OIDC
    address.refresh_token = _make_expired_token()

    def fake_post(**kwargs):
        pytest.fail("httpx2.post should not be called - there's no password to send")

    monkeypatch.setattr(httpx2, "post", fake_post)

    remote = Remote(remotes, address)
    with pytest.raises(RemoteSsoRequiredError):
        remote.login()


@pytest.mark.unit
def test_login_raises_sso_required_for_an_oidc_remote_when_the_refresh_call_fails(address, remotes, monkeypatch):
    """Regression-shaped: a valid-looking but server-rejected refresh_token must raise
    RemoteSsoRequiredError, mirroring test_login_falls_back_to_full_login_when_refresh_request_fails
    for a password remote - but here there's no password to fall back to, only an interactive login."""
    address.auth_method = AuthType.OIDC
    address.refresh_token = _make_token()

    def fake_post(url, json, timeout):
        raise httpx2.ConnectError("boom")

    monkeypatch.setattr(httpx2, "post", fake_post)

    remote = Remote(remotes, address)
    with pytest.raises(RemoteSsoRequiredError):
        remote.login()


@pytest.mark.unit
def test_login_for_an_oidc_remote_with_a_valid_refresh_token_refreshes_normally(address, remotes, monkeypatch):
    """The cheap refresh path is entirely agnostic to auth_method - an OIDC remote with a
    still-valid Pytre refresh_token behaves exactly like a password remote, no browser involved."""
    address.auth_method = AuthType.OIDC
    address.refresh_token = _make_token()
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        return _FakeResponse({"access_token": "new-access"})

    monkeypatch.setattr(httpx2, "post", fake_post)

    result = Remote(remotes, address).login()

    assert result is True
    assert captured["url"] == "http://127.0.0.1:8000/refresh"
    assert address.access_token == "new-access"


@pytest.mark.unit
def test_login_for_a_basic_remote_is_unaffected_by_the_sso_branch(address, remotes, monkeypatch):
    """Non-regression: auth_method defaults to AuthType.BASIC, so every pre-existing password
    remote must keep going straight to /login, never hitting the new OIDC branch."""
    assert address.auth_method is AuthType.BASIC
    monkeypatch.setattr(httpx2, "post", lambda **kwargs: _FakeResponse({"refresh_token": "r1", "access_token": "a1"}))

    result = Remote(remotes, address).login()

    assert result is True
    assert address.refresh_token == "r1"
    assert address.access_token == "a1"


# ================================================================================
# Remote.auth_headers
# ================================================================================


@pytest.mark.unit
def test_auth_headers_includes_bearer_token(address, remotes):
    address.access_token = "abc123"

    assert Remote(remotes, address).auth_headers == {"Authorization": "Bearer abc123"}


@pytest.mark.unit
def test_auth_headers_with_no_token_omits_authorization_header(address, remotes):
    """Regression: `Authorization: Bearer ` (empty token, trailing space) is rejected by
    httpx2/h11 as an illegal header value before the request ever reaches the server -
    confirmed empirically against a real HTTP server (LocalProtocolError). Omitting the
    header entirely is syntactically valid and the server already treats a missing header
    the same as an invalid one (401), so "missing" and "expired" still share the same
    retry path in get_authenticated()/execute()."""
    assert Remote(remotes, address).auth_headers == {}


# ================================================================================
# Remote.get_authenticated
# ================================================================================


@pytest.mark.unit
def test_get_authenticated_returns_response_without_retry_on_success(address, remotes, monkeypatch):
    """A valid access_token already in hand: get_authenticated() does exactly one GET, no
    login, no retry."""
    address.access_token = _make_token()
    calls = []

    def fake_get(**kwargs):
        calls.append(kwargs)
        return _FakeResponse({"ok": True}, status_code=200)

    monkeypatch.setattr(httpx2, "get", fake_get)

    response = Remote(remotes, address).get_authenticated("http://example.com:8000/servers")

    assert response.json() == {"ok": True}
    assert len(calls) == 1  # regression: used to always GET twice, even on immediate success


@pytest.mark.unit
def test_get_authenticated_sends_bearer_header_and_params(address, remotes, monkeypatch):
    address.access_token = _make_token()
    captured = {}

    def fake_get(url, params, headers, timeout):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return _FakeResponse(status_code=200)

    monkeypatch.setattr(httpx2, "get", fake_get)

    Remote(remotes, address).get_authenticated("http://example.com:8000/queries", params={"server_id": "s1"})

    assert captured["url"] == "http://example.com:8000/queries"
    assert captured["params"] == {"server_id": "s1"}
    assert captured["headers"] == {"Authorization": f"Bearer {address.access_token}"}


@pytest.mark.unit
def test_get_authenticated_logs_in_first_when_no_valid_token_yet(address, remotes, monkeypatch, auto_login_ok):
    """Regression: the very first call, before any access_token has ever been obtained, must
    not attempt a GET with an empty/invalid Bearer header - get_authenticated() must log in
    first and only then GET, with the freshly obtained token."""
    get_calls = []

    def fake_get(url, params, headers, timeout):
        get_calls.append(headers)
        return _FakeResponse({"ok": True}, status_code=200)

    monkeypatch.setattr(httpx2, "get", fake_get)

    Remote(remotes, address).get_authenticated("http://example.com:8000/servers")

    assert len(get_calls) == 1
    assert get_calls[0] == {"Authorization": "Bearer a1"}


@pytest.mark.unit
def test_get_authenticated_relogs_in_and_retries_once_on_401(address, remotes, monkeypatch):
    address.access_token = _make_token()
    get_responses = iter([_FakeResponse(status_code=401), _FakeResponse({"ok": True}, status_code=200)])
    monkeypatch.setattr(httpx2, "get", lambda **kwargs: next(get_responses))
    monkeypatch.setattr(
        httpx2, "post", lambda **kwargs: _FakeResponse({"refresh_token": "r1", "access_token": "new-token"})
    )

    response = Remote(remotes, address).get_authenticated("http://example.com:8000/servers")

    assert response.json() == {"ok": True}
    assert address.access_token == "new-token"


@pytest.mark.unit
def test_get_authenticated_raises_remote_request_error_if_login_fails_after_401(address, remotes, monkeypatch):
    monkeypatch.setattr(httpx2, "get", lambda **kwargs: _FakeResponse(status_code=401))

    def fake_post(**kwargs):
        raise httpx2.ConnectError("boom")

    monkeypatch.setattr(httpx2, "post", fake_post)

    with pytest.raises(RemoteRequestError):
        Remote(remotes, address).get_authenticated("http://example.com:8000/servers")


@pytest.mark.unit
def test_get_authenticated_raises_remote_auth_error_if_login_fails_with_bad_credentials_after_401(
    address, remotes, monkeypatch
):
    """Distinct from the network-failure case above - a 401 on /login itself means the
    credentials are wrong, not that the remote is unreachable."""
    monkeypatch.setattr(httpx2, "get", lambda **kwargs: _FakeResponse(status_code=401))
    error = httpx2.HTTPStatusError("401", request=None, response=None)  # type: ignore
    monkeypatch.setattr(httpx2, "post", lambda **kwargs: _FakeResponse(status_error=error))

    with pytest.raises(RemoteAuthError):
        Remote(remotes, address).get_authenticated("http://example.com:8000/servers")


# ================================================================================
# Remote.refresh_servers
# ================================================================================


@pytest.mark.unit
def test_refresh_servers_populates_servers_on_success(address, remotes, monkeypatch, auto_login_ok):
    servers_data = [{"id": "s1", "description": "Server 1"}, {"id": "s2", "description": "Server 2"}]
    payload = _servers_payload(servers_data)
    monkeypatch.setattr(httpx2, "get", lambda **kwargs: _FakeResponse(payload, status_code=200))

    remote = Remote(remotes, address=address)
    result = remote.refresh_servers()

    assert result == remote.servers
    assert [s.id_on_remote for s in remote.servers] == ["s1", "s2"]
    assert all(s.remote is remote for s in remote.servers)
    assert remote.last_error is None


@pytest.mark.unit
def test_refresh_servers_populates_user_infos_on_success(address, remotes, monkeypatch, auto_login_ok):
    payload = _servers_payload([], user_infos={"username": "alice", "admin": False})
    monkeypatch.setattr(httpx2, "get", lambda **kwargs: _FakeResponse(payload, status_code=200))

    remote = Remote(remotes, address=address)
    remote.refresh_servers()

    assert remote.user_infos == {"username": "alice", "admin": False}


@pytest.mark.unit
def test_refresh_servers_sends_correct_url_and_auth_header(address, remotes, monkeypatch):
    address.access_token = _make_token()
    captured = {}

    def fake_get(url, params, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        return _FakeResponse(_servers_payload([]), status_code=200)

    monkeypatch.setattr(httpx2, "get", fake_get)

    Remote(remotes, address=address).refresh_servers()

    assert captured["url"] == "http://127.0.0.1:8000/servers"
    assert captured["headers"] == {"Authorization": f"Bearer {address.access_token}"}


@pytest.mark.unit
def test_refresh_servers_keeps_previous_list_on_request_error(address, remotes, monkeypatch):
    """A valid access_token is required here so get_authenticated() GETs directly instead of
    calling login() first - otherwise the failure below would never actually be reached."""
    address.access_token = _make_token()

    def fake_get(**kwargs):
        raise httpx2.ConnectError("boom")

    monkeypatch.setattr(httpx2, "get", fake_get)

    remote = Remote(remotes, address=address)
    remote.servers = [RemoteServer(id="old", description="Old", remote=remote)]

    result = remote.refresh_servers()

    assert result == remote.servers
    assert [s.id_on_remote for s in remote.servers] == ["old"]
    assert isinstance(remote.last_error, RemoteRequestError)


@pytest.mark.unit
def test_refresh_servers_keeps_previous_list_on_http_status_error(address, remotes, monkeypatch):
    address.access_token = _make_token()
    error = httpx2.HTTPStatusError("500", request=None, response=None)  # type: ignore
    monkeypatch.setattr(httpx2, "get", lambda **kwargs: _FakeResponse(status_code=200, status_error=error))

    remote = Remote(remotes, address=address)
    remote.servers = [RemoteServer(id="old", description="Old", remote=remote)]

    result = remote.refresh_servers()

    assert [s.id_on_remote for s in remote.servers] == ["old"]
    assert isinstance(remote.last_error, RemoteHTTPStatusError)


@pytest.mark.unit
def test_refresh_servers_keeps_previous_list_and_sets_remote_auth_error_on_relogin_failure(
    address, remotes, monkeypatch
):
    """A 401 on /servers triggers a relogin - if that relogin fails because credentials are
    invalid, refresh_servers() must surface it as RemoteAuthError (caught via the base
    RemoteError branch), not misreport it as a generic connectivity failure."""
    address.access_token = _make_token()
    monkeypatch.setattr(httpx2, "get", lambda **kwargs: _FakeResponse(status_code=401))
    error = httpx2.HTTPStatusError("401", request=None, response=None)  # type: ignore
    monkeypatch.setattr(httpx2, "post", lambda **kwargs: _FakeResponse(status_error=error))

    remote = Remote(remotes, address=address)
    remote.servers = [RemoteServer(id="old", description="Old", remote=remote)]

    result = remote.refresh_servers()

    assert result == remote.servers
    assert [s.id_on_remote for s in remote.servers] == ["old"]
    assert isinstance(remote.last_error, RemoteAuthError)


@pytest.mark.unit
def test_refresh_servers_success_clears_last_error(address, remotes, monkeypatch, auto_login_ok):
    remote = Remote(remotes, address=address)
    remote.last_error = RemoteError("previous failure")

    monkeypatch.setattr(httpx2, "get", lambda **kwargs: _FakeResponse(_servers_payload([]), status_code=200))
    remote.refresh_servers()

    assert remote.last_error is None


# ================================================================================
# RemoteServer.get_serialized_queries
# ================================================================================


@pytest.fixture
def remote_server(address, remotes) -> RemoteServer:
    remote = Remote(remotes, address)
    return RemoteServer(id="SRV1", description="Server 1", remote=remote)


@pytest.mark.unit
def test_get_serialized_queries_populates_on_success(remote_server, monkeypatch, auto_login_ok):
    queries_data = [{"name": "Q1"}, {"name": "Q2"}]
    monkeypatch.setattr(httpx2, "get", lambda **kwargs: _FakeResponse(queries_data, status_code=200))

    result = remote_server.get_serialized_queries(DisplayFormat())

    assert result == queries_data
    assert remote_server.serialized_queries == queries_data
    assert remote_server.last_error is None


@pytest.mark.unit
def test_get_serialized_queries_sends_server_id_and_auth_header(remote_server, monkeypatch):
    remote_server.address.access_token = _make_token()
    captured = {}

    def fake_get(url, params, headers, timeout):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return _FakeResponse([], status_code=200)

    monkeypatch.setattr(httpx2, "get", fake_get)

    remote_server.get_serialized_queries(DisplayFormat())

    assert captured["url"] == "http://127.0.0.1:8000/queries"
    assert captured["params"] == {
        "server_id": "SRV1",
        "display_format": json.dumps({"date_format": "%d/%m/%Y", "decimal_separator": ","}),
    }
    assert captured["headers"] == {"Authorization": f"Bearer {remote_server.address.access_token}"}


@pytest.mark.unit
def test_get_serialized_queries_keeps_previous_list_on_request_error(remote_server, monkeypatch):
    """A valid access_token is required here so get_authenticated() GETs directly instead of
    calling login() first - otherwise the failure below would never actually be reached."""
    remote_server.address.access_token = _make_token()

    def fake_get(**kwargs):
        raise httpx2.ConnectError("boom")

    monkeypatch.setattr(httpx2, "get", fake_get)
    remote_server.serialized_queries = [{"name": "old"}]

    result = remote_server.get_serialized_queries(DisplayFormat())

    assert result == [{"name": "old"}]
    assert isinstance(remote_server.last_error, RemoteRequestError)


@pytest.mark.unit
def test_get_serialized_queries_keeps_previous_list_on_http_status_error(remote_server, monkeypatch):
    remote_server.address.access_token = _make_token()
    error = httpx2.HTTPStatusError("500", request=None, response=None)  # type: ignore
    monkeypatch.setattr(httpx2, "get", lambda **kwargs: _FakeResponse(status_code=200, status_error=error))
    remote_server.serialized_queries = [{"name": "old"}]

    result = remote_server.get_serialized_queries(DisplayFormat())

    assert result == [{"name": "old"}]
    assert isinstance(remote_server.last_error, RemoteHTTPStatusError)


@pytest.mark.unit
def test_get_serialized_queries_keeps_previous_list_and_sets_remote_auth_error_on_relogin_failure(
    remote_server, monkeypatch
):
    """A 401 on /queries triggers a relogin - if that relogin fails because credentials are
    invalid, get_serialized_queries() must surface it as RemoteAuthError (caught via the base
    RemoteError branch), not misreport it as a generic connectivity failure."""
    remote_server.address.access_token = _make_token()
    monkeypatch.setattr(httpx2, "get", lambda **kwargs: _FakeResponse(status_code=401))
    error = httpx2.HTTPStatusError("401", request=None, response=None)  # type: ignore
    monkeypatch.setattr(httpx2, "post", lambda **kwargs: _FakeResponse(status_error=error))
    remote_server.serialized_queries = [{"name": "old"}]

    result = remote_server.get_serialized_queries(DisplayFormat())

    assert result == [{"name": "old"}]
    assert isinstance(remote_server.last_error, RemoteAuthError)


# ================================================================================
# Remote construction
# ================================================================================


@pytest.mark.unit
def test_remote_init_does_not_call_http_when_load_is_false(address, remotes, monkeypatch):
    monkeypatch.setattr(httpx2, "get", lambda **kwargs: pytest.fail("httpx2.get should not be called"))

    remote = Remote(remotes, address=address, load=False)

    assert remote.servers == []


@pytest.mark.unit
def test_remote_init_with_load_true_fetches_servers(address, remotes, monkeypatch, auto_login_ok):
    payload = _servers_payload([{"id": "s1", "description": "Server 1"}])
    monkeypatch.setattr(httpx2, "get", lambda **kwargs: _FakeResponse(payload, status_code=200))

    remote = Remote(remotes, address=address, load=True)

    assert [s.id_on_remote for s in remote.servers] == ["s1"]


# ================================================================================
# Remote.save / Remote.delete without a Remotes collection
# ================================================================================


@pytest.mark.unit
def test_save_raises_runtime_error_when_remotes_is_none(address):
    """The hollow Remote(None, address) built by RemoteServer.deserialize() must never be
    saved/deleted - it only exists to carry an address across the execution round-trip."""
    with pytest.raises(RuntimeError):
        Remote(None, address).save()


@pytest.mark.unit
def test_delete_raises_runtime_error_when_remotes_is_none(address):
    with pytest.raises(RuntimeError):
        Remote(None, address).delete()


# ================================================================================
# Remotes
# ================================================================================


@pytest.fixture
def no_remote_servers(monkeypatch):
    """Every remote reports an empty server list - avoids real HTTP calls for tests that
    aren't exercising refresh_servers() itself (already covered above)."""
    monkeypatch.setattr(httpx2, "get", lambda **kwargs: _FakeResponse([]))


@pytest.mark.unit
def test_remotes_init_without_remotes_is_empty(remotes_file):
    assert Remotes(remotes_file).remotes_dict == {}


@pytest.mark.unit
def test_remotes_init_builds_one_remote_per_address(no_remote_servers, remotes_file):
    addr1 = RemoteAddress(url="http://a", port=8000, username="alice")
    addr2 = RemoteAddress(url="http://b", port=8001, username="bob")
    _write_remotes_file(remotes_file, [addr1, addr2])

    remotes = Remotes(remotes_file)

    assert {r.address.url for r in remotes.remotes_dict.values()} == {addr1.url, addr2.url}
    assert set(remotes.remotes_dict.keys()) == {addr1.uuid, addr2.uuid}


@pytest.mark.unit
def test_remotes_init_does_not_fetch_servers(monkeypatch, remotes_file):
    """Regression: Remotes.__init__ used to eagerly call get_all_servers(reload=True), a
    blocking HTTP round trip per configured remote done twice in a row at Pytre_Client
    startup (once here, once from App.refresh_servers() right after) - init must now only
    load the remotes list from the remotes json file, never touch the network."""
    addr = RemoteAddress(url="http://a", port=8000, username="alice")
    _write_remotes_file(remotes_file, [addr])
    monkeypatch.setattr(httpx2, "get", lambda **kwargs: pytest.fail("httpx2.get should not be called"))

    remotes = Remotes(remotes_file)

    assert remotes.servers_dict == {}
    assert list(remotes.remotes_dict.values())[0].servers == []


@pytest.mark.unit
def test_get_all_servers_reload_refreshes_every_remote_and_merges_them(monkeypatch, remotes_file, auto_login_ok):
    addr1 = RemoteAddress(url="http://127.0.0.1", port=8000, username="alice", password="secret")
    addr2 = RemoteAddress(url="http://127.0.0.1", port=8001, username="bob", password="secret")
    _write_remotes_file(remotes_file, [addr1, addr2])
    payload = _servers_payload([{"id": "s1", "description": "Server 1"}])
    monkeypatch.setattr(httpx2, "get", lambda **kwargs: _FakeResponse(payload))

    remotes = Remotes(remotes_file)
    servers = remotes.get_all_servers(reload=True)

    assert all(len(r.servers) == 1 for r in remotes.remotes_dict.values())
    assert len(servers) == 2  # one "s1" server per remote, merged by RemoteServer.id (which disambiguates them)


@pytest.mark.unit
def test_get_all_servers_without_reload_assembles_from_current_state(monkeypatch, remotes_file, auto_login_ok):
    """No network call: assembles servers_dict from whatever each remote already holds in
    memory, rather than returning a possibly-empty separately-tracked cache."""
    addr = RemoteAddress(url="http://127.0.0.1", port=8000, username="alice", password="secret")
    _write_remotes_file(remotes_file, [addr])
    payload = _servers_payload([{"id": "s1", "description": "Server 1"}])
    monkeypatch.setattr(httpx2, "get", lambda **kwargs: _FakeResponse(payload))
    remotes = Remotes(remotes_file)
    remotes.get_all_servers(reload=True)  # populate remote.servers once, since init no longer does it

    monkeypatch.setattr(httpx2, "get", lambda **kwargs: pytest.fail("httpx2.get should not be called"))
    servers = remotes.get_all_servers()

    assert len(servers) == 1


@pytest.mark.unit
def test_get_all_remotes_with_reload_rereads_file(no_remote_servers, remotes_file):
    remotes = Remotes(remotes_file)
    assert remotes.remotes_dict == {}

    addr = RemoteAddress(url="http://a", port=8000, username="alice")
    _write_remotes_file(remotes_file, [addr])

    result = remotes.get_all_remotes(reload=True)

    assert set(result.keys()) == {addr.uuid}


@pytest.mark.unit
def test_get_all_remotes_without_reload_does_not_reread_file(no_remote_servers, remotes_file):
    remotes = Remotes(remotes_file)

    addr = RemoteAddress(url="http://a", port=8000, username="alice")
    _write_remotes_file(remotes_file, [addr])

    result = remotes.get_all_remotes()

    assert result == {}


# ================================================================================
# Remotes.find_remote_from_uuid / find_remote_from_id
# ================================================================================


@pytest.mark.unit
def test_find_remote_from_id_returns_matching_remote_case_insensitive(no_remote_servers, address, remotes_file):
    _write_remotes_file(remotes_file, [address])
    remotes = Remotes(remotes_file)

    found = remotes.find_remote_from_id(address.id.upper())

    assert found is not None
    assert found.address.url == address.url


@pytest.mark.unit
def test_find_remote_from_id_returns_none_when_not_found(no_remote_servers, remotes_file):
    remotes = Remotes(remotes_file)

    assert remotes.find_remote_from_id("unknown") is None


@pytest.mark.unit
def test_find_remote_from_uuid_returns_matching_remote(no_remote_servers, address, remotes_file):
    _write_remotes_file(remotes_file, [address])
    remotes = Remotes(remotes_file)

    found = remotes.find_remote_from_uuid(address.uuid)

    assert found is not None
    assert found.address.url == address.url


@pytest.mark.unit
def test_find_remote_from_uuid_returns_none_when_not_found(no_remote_servers, remotes_file):
    remotes = Remotes(remotes_file)

    assert remotes.find_remote_from_uuid("unknown") is None


# ================================================================================
# Remote.save
# ================================================================================


@pytest.mark.unit
def test_save_adds_new_remote(no_remote_servers, address, remotes_file):
    remotes = Remotes(remotes_file)
    remote = Remote(remotes, address)

    remote.save()

    assert [r.address.id for r in remotes.remotes_dict.values()] == [address.id]
    saved = _read_remotes_file(remotes_file)
    assert {k: _comparable(v) for k, v in saved.items()} == {
        address.uuid: _comparable({k: v for k, v in address.to_dict().items() if k != "uuid"})
    }


@pytest.mark.unit
def test_save_raises_if_new_remote_id_collides_with_existing(no_remote_servers, address, remotes_file):
    """A brand new remote (a different uuid) whose id collides with one already configured
    must be rejected, not silently added as a duplicate."""
    _write_remotes_file(remotes_file, [address])
    remotes = Remotes(remotes_file)
    colliding = Remote(remotes, RemoteAddress(url=address.url, port=address.port, username=address.username))

    with pytest.raises(LookupError):
        colliding.save()


@pytest.mark.unit
def test_save_overwrites_entry_with_same_uuid_after_editing_host(no_remote_servers, address, remotes_file):
    """Regression: editing host/port/username changes .id but must not change .uuid -
    save() must still find and replace the original entry by uuid, not create a duplicate
    (this used to fail two different ways: uuid not surviving a reload, and save() only
    inserting when no match was found instead of always overwriting by uuid)."""
    _write_remotes_file(remotes_file, [address])
    remotes = Remotes(remotes_file)
    found = remotes.find_remote_from_uuid(address.uuid)
    assert found is not None

    found.address.url = "http://new-host"
    found.address.description = "Updated"
    found.save()

    assert len(remotes.remotes_dict) == 1
    saved = remotes.remotes_dict[address.uuid]
    assert saved.address.url == "http://new-host"
    assert saved.address.description == "Updated"


@pytest.mark.unit
def test_save_with_reload_false_does_not_replace_the_in_memory_remotes_dict(no_remote_servers, address, remotes_file):
    """Regression (Lot 7): save() used to always reload remotes_dict from disk first, replacing
    every Remote instance currently held in memory - deadly when called from inside code that's
    iterating remotes_dict itself (Remote.login() persisting a rotated token from inside
    Remotes.get_all_servers(reload=True)'s own loop silently corrupted that same iteration).
    reload=False must leave remotes_dict's existing dict object and Remote instances untouched."""
    remotes = Remotes(remotes_file)
    remote = Remote(remotes, address)
    remotes.remotes_dict[address.uuid] = remote
    before = remotes.remotes_dict

    remote.save(reload=False)

    assert remotes.remotes_dict is before  # same dict object, never replaced
    assert remotes.remotes_dict[address.uuid] is remote  # same Remote instance, never replaced


@pytest.mark.unit
def test_save_with_reload_false_still_checks_for_id_collisions_against_current_memory(
    no_remote_servers, address, remotes_file
):
    """reload=False skips re-reading remotes_dict from disk, but the id-collision check itself
    still runs - just against whatever is already in memory, which is enough since the only
    caller using reload=False (Remote.login()) never changes url/port/username anyway."""
    remotes = Remotes(remotes_file)
    remotes.remotes_dict[address.uuid] = Remote(remotes, address)
    colliding = Remote(remotes, RemoteAddress(url=address.url, port=address.port, username=address.username))
    remotes.remotes_dict[colliding.uuid] = colliding

    with pytest.raises(LookupError):
        colliding.save(reload=False)


# ================================================================================
# Remote.delete
# ================================================================================


@pytest.mark.unit
def test_delete_removes_matching_remote(no_remote_servers, address, remotes_file):
    _write_remotes_file(remotes_file, [address])
    remotes = Remotes(remotes_file)
    remote = remotes.find_remote_from_uuid(address.uuid)
    assert remote is not None

    remote.delete()

    assert remotes.remotes_dict == {}
    assert _read_remotes_file(remotes_file) == {}


@pytest.mark.unit
def test_delete_raises_if_not_found(no_remote_servers, address, remotes_file):
    remotes = Remotes(remotes_file)
    orphan = Remote(remotes, address)  # never saved, its uuid is unknown to remotes

    with pytest.raises(LookupError):
        orphan.delete()


# ================================================================================
# Remotes.csv_import / csv_export
# ================================================================================


@pytest.mark.unit
def test_csv_import_rejects_multi_character_delimiter(no_remote_servers, tmp_path, remotes_file):
    remotes = Remotes(remotes_file)
    csv_file = tmp_path / "remotes.csv"
    csv_file.write_text("url;port;username;description\n", encoding="utf-8-sig")

    with pytest.raises(ValueError, match="single character"):
        remotes.csv_import(csv_file, delimiter=";;")


@pytest.mark.unit
def test_csv_import_raises_if_file_missing(no_remote_servers, tmp_path, remotes_file):
    remotes = Remotes(remotes_file)

    with pytest.raises(FileNotFoundError):
        remotes.csv_import(tmp_path / "missing.csv")


@pytest.mark.unit
def test_csv_import_raises_if_columns_missing(no_remote_servers, tmp_path, remotes_file):
    remotes = Remotes(remotes_file)
    csv_file = tmp_path / "remotes.csv"
    csv_file.write_text("url;port\nhttp://a;8000\n", encoding="utf-8-sig")

    with pytest.raises(ValueError, match="Missing columns"):
        remotes.csv_import(csv_file)


@pytest.mark.unit
def test_csv_import_adds_new_remotes_and_persists(no_remote_servers, tmp_path, remotes_file):
    remotes = Remotes(remotes_file)
    csv_file = tmp_path / "remotes.csv"
    csv_file.write_text(
        "url;port;username;description\nhttp://a;8000;alice;Remote A\nhttp://b;8001;bob;Remote B\n",
        encoding="utf-8-sig",
    )

    remotes.csv_import(csv_file)

    assert sorted(r.address.url for r in remotes.remotes_dict.values()) == ["http://a", "http://b"]
    assert len(_read_remotes_file(remotes_file)) == 2  # persisted


@pytest.mark.unit
def test_csv_import_skips_invalid_rows(no_remote_servers, tmp_path, remotes_file):
    remotes = Remotes(remotes_file)
    csv_file = tmp_path / "remotes.csv"
    csv_file.write_text(
        "url;port;username;description\nhttp://a;8000;alice;Remote A\n;8001;bob;Remote B\n",
        encoding="utf-8-sig",
    )

    remotes.csv_import(csv_file)

    assert [r.address.url for r in remotes.remotes_dict.values()] == ["http://a"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_row",
    ["http://b;8001;bob;s3cret", "http://b;8001;bob;Remote; B;s3cret;extra"],
    ids=["missing-value", "extra-value"],
)
def test_csv_import_ignores_a_row_not_matching_the_header(no_remote_servers, tmp_path, remotes_file, caplog, bad_row):
    """A missing delimiter would put the password into the description, shown in clear: the row is refused"""
    remotes = Remotes(remotes_file)
    csv_file = tmp_path / "remotes.csv"
    csv_file.write_text(
        f"url;port;username;description;password\nhttp://a;8000;alice;Remote A;pwd\n{bad_row}\n",
        encoding="utf-8-sig",
    )

    with caplog.at_level(logging.WARNING):
        remotes.csv_import(csv_file)

    assert [r.address.url for r in remotes.remotes_dict.values()] == ["http://a"]
    assert any("Row 3 ignored: its number of values doesn't match the header's" in r.message for r in caplog.records)


@pytest.mark.unit
def test_csv_import_updates_existing_remote_matching_id(no_remote_servers, address, tmp_path, remotes_file):
    """Import is an upsert: a CSV row whose id matches an existing remote replaces it (and
    keeps its uuid, see Remotes.csv_import), it isn't just ignored in favor of the entry
    already configured, nor added as a second entry under a new uuid."""
    _write_remotes_file(remotes_file, [address])
    remotes = Remotes(remotes_file)
    csv_file = tmp_path / "remotes.csv"
    csv_file.write_text(
        f"url;port;username;description\n{address.url};{address.port};{address.username};Updated\n",
        encoding="utf-8-sig",
    )

    remotes.csv_import(csv_file)

    assert len(remotes.remotes_dict) == 1
    updated = remotes.remotes_dict[address.uuid]
    assert updated.address.description == "Updated"


@pytest.mark.unit
def test_csv_import_keeps_existing_password_when_row_has_none(no_remote_servers, tmp_path, remotes_file):
    """A CSV row without a password column (or with the column present but empty) must not
    wipe out an already-configured password for that remote - only a non-empty password/
    encrypted_pwd value should ever replace it."""
    address = RemoteAddress(url="http://a", port=8000, username="alice", password="s3cret")
    _write_remotes_file(remotes_file, [address])
    remotes = Remotes(remotes_file)
    csv_file = tmp_path / "remotes.csv"
    csv_file.write_text(
        f"url;port;username;description\n{address.url};{address.port};{address.username};Updated\n",
        encoding="utf-8-sig",
    )

    remotes.csv_import(csv_file)

    updated = remotes.remotes_dict[address.uuid]
    assert updated.address.password == "s3cret"


@pytest.mark.unit
def test_csv_import_replaces_password_when_row_has_one(no_remote_servers, tmp_path, remotes_file):
    address = RemoteAddress(url="http://a", port=8000, username="alice", password="old-pwd")
    _write_remotes_file(remotes_file, [address])
    remotes = Remotes(remotes_file)
    csv_file = tmp_path / "remotes.csv"
    csv_file.write_text(
        f"url;port;username;password;description\n{address.url};{address.port};{address.username};new-pwd;Updated\n",
        encoding="utf-8-sig",
    )

    remotes.csv_import(csv_file)

    updated = remotes.remotes_dict[address.uuid]
    assert updated.address.password == "new-pwd"


@pytest.mark.unit
def test_csv_export_rejects_multi_character_delimiter(no_remote_servers, tmp_path, remotes_file):
    remotes = Remotes(remotes_file)

    with pytest.raises(ValueError, match="single character"):
        remotes.csv_export(tmp_path / "out.csv", delimiter=";;")


@pytest.mark.unit
def test_csv_export_raises_if_file_exists_and_overwrite_is_false(no_remote_servers, tmp_path, remotes_file):
    remotes = Remotes(remotes_file)
    existing = tmp_path / "out.csv"
    existing.write_text("")

    with pytest.raises(FileExistsError):
        remotes.csv_export(existing, overwrite=False)


@pytest.mark.unit
def test_csv_export_writes_current_remotes(no_remote_servers, address, tmp_path, remotes_file):
    _write_remotes_file(remotes_file, [address])
    remotes = Remotes(remotes_file)
    out_file = tmp_path / "out.csv"

    remotes.csv_export(out_file, overwrite=True)

    content = out_file.read_text(encoding="utf-8-sig")
    assert "url;port;username;encrypted_pwd;allow_unsecure_channel;description" in content
    assert f"{address.url};{address.port};{address.username};" in content
    assert f";{address.description}" in content


@pytest.mark.unit
def test_csv_export_import_round_trips_allow_unsecure_channel(no_remote_servers, tmp_path, remotes_file):
    """Regression: allow_unsecure_channel used to be silently dropped by csv_export() - a
    remote exported then re-imported (e.g. as a backup/migration) would lose the setting."""
    address = RemoteAddress(url="http://a", port=8000, username="alice", allow_unsecure_channel=True)
    _write_remotes_file(remotes_file, [address])
    remotes = Remotes(remotes_file)
    csv_file = tmp_path / "out.csv"

    remotes.csv_export(csv_file, overwrite=True)
    remotes.csv_import(csv_file)

    reimported = remotes.remotes_dict[address.uuid]
    assert reimported.address.allow_unsecure_channel is True


# ================================================================================
# RemoteServer construction / properties
# ================================================================================


@pytest.mark.unit
def test_remote_server_address_property_delegates_to_remote(address, remotes):
    remote = Remote(remotes, address)
    server = RemoteServer(id="SRV1", description="Server 1", remote=remote)

    assert server.address is remote.address


@pytest.mark.unit
def test_remote_server_auth_headers_delegates_to_remote(address, remotes):
    address.access_token = "abc123"
    remote = Remote(remotes, address)
    server = RemoteServer(id="SRV1", description="Server 1", remote=remote)

    assert server._auth_headers == {"Authorization": "Bearer abc123"}


@pytest.mark.unit
def test_remote_server_id_combines_id_on_remote_and_remote_address_id(address, remotes):
    """RemoteServer.id disambiguates servers sharing the same id_on_remote across different
    remotes, once merged into a flat list client-side."""
    remote = Remote(remotes, address)
    server = RemoteServer(id="SRV1", description="Server 1", remote=remote)

    assert server.id == f"SRV1|{address.id}"


# ================================================================================
# RemoteServer.serialize / deserialize
# ================================================================================


@pytest.mark.unit
def test_remote_server_serialize(address, remotes):
    remote = Remote(remotes, address)
    server = RemoteServer(id="SRV1", description="Server 1", remote=remote)

    data = server.serialize()

    assert data | {"address": _comparable(data["address"])} == {
        "id_on_remote": "SRV1",
        "description": "Server 1",
        "address": _comparable(address.to_dict()),
        "user_infos": None,
    }


@pytest.mark.unit
def test_remote_server_deserialize(address):
    data = {"id_on_remote": "SRV1", "description": "Server 1", "address": address.to_dict()}

    server = RemoteServer.deserialize(data)

    assert server.id_on_remote == "SRV1"
    assert server.description == "Server 1"
    assert server.address.url == address.url
    assert server.address.port == address.port
    assert server.address.username == address.username


@pytest.mark.unit
def test_remote_server_deserialize_builds_a_remote_with_no_remotes_collection():
    """Only used for the execution round-trip (see Query.serialize()) - the reconstructed
    Remote is hollow (Remote(None, address)), so save()/delete() must raise on it, even
    though nothing on this round-trip ever calls them."""
    data = {
        "id_on_remote": "SRV1",
        "description": "Server 1",
        "address": RemoteAddress.from_dict({"url": "http://a", "port": 8000, "username": "alice"}).to_dict(),
    }

    server = RemoteServer.deserialize(data)

    assert server.remote.remotes is None
    with pytest.raises(RuntimeError):
        server.remote.save()


@pytest.mark.unit
def test_remote_server_serialize_deserialize_round_trip(address, remotes):
    remote = Remote(remotes, address)
    original = RemoteServer(id="SRV1", description="Server 1", remote=remote)

    rebuilt = RemoteServer.deserialize(original.serialize())

    assert rebuilt.id_on_remote == original.id_on_remote
    assert rebuilt.description == original.description
    assert rebuilt.address.url == original.address.url
    assert rebuilt.address.port == original.address.port
    assert rebuilt.address.username == original.address.username


@pytest.mark.unit
def test_remote_server_serialize_carries_user_infos(address, remotes):
    remote = Remote(remotes, address)
    remote.user_infos = {"username": "alice", "attribs_cust": {"dept": "IT"}}
    original = RemoteServer(id="SRV1", description="Server 1", remote=remote)

    data = original.serialize()
    rebuilt = RemoteServer.deserialize(data)

    assert data["user_infos"] == remote.user_infos
    assert rebuilt.remote.user_infos == remote.user_infos


@pytest.mark.unit
def test_remote_server_deserialize_without_user_infos_defaults_to_none(address):
    data = {"id_on_remote": "SRV1", "description": "Server 1", "address": address.to_dict()}

    assert RemoteServer.deserialize(data).remote.user_infos is None


# ================================================================================
# RemoteServer.execute - executions by jobs (/jobs)
# ================================================================================


class _FakeJobsServer:
    """Answers the requests of a RemoteJob through httpx2.MockTransport, from scripted answers per method and path:
    each answer is used once, the last one again for every following request. An answer is (status, body) - body a
    dict sent as JSON, bytes sent as is, or None - or an exception raised instead of answering."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.answers: dict[tuple[str, str], list] = {
            ("POST", "/jobs"): [(201, {"job_id": "j1", "status": "running"})],
            ("GET", "/jobs/j1"): [(200, {"status": "ready"})],
            ("GET", "/jobs/j1/batches/1"): [(200, Batch(1, [], last=True, columns=[]).encode())],
            ("DELETE", "/jobs/j1"): [(204, None)],
        }

    def script(self, method: str, path: str, *answers) -> None:
        self.answers[(method, path)] = list(answers)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        answers = self.answers.get((request.method, request.url.path))
        if not answers:
            return httpx2.Response(404, json={"detail": "Unknown job"})

        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if callable(answer):
            answer = answer(request)
        if isinstance(answer, BaseException):
            raise answer

        status, body = answer
        if isinstance(body, bytes):
            return httpx2.Response(status, content=body, headers={"content-type": "application/json"})
        return httpx2.Response(status) if body is None else httpx2.Response(status, json=body)

    @property
    def calls(self) -> list[tuple[str, str]]:
        return [(request.method, request.url.path) for request in self.requests]


@pytest.fixture
def jobs_server(monkeypatch) -> _FakeJobsServer:
    """Every httpx2.Client created by RemoteJob talks to a _FakeJobsServer. /health answers ok."""
    server = _FakeJobsServer()
    real_client = httpx2.Client
    monkeypatch.setattr(
        httpx2, "Client", lambda **kwargs: real_client(transport=httpx2.MockTransport(server.handle), **kwargs)
    )
    _mock_health_check_ok(monkeypatch)
    return server


@pytest.fixture
def no_retry_wait(monkeypatch) -> list[float]:
    """RemoteJob doesn't wait between two tries: the delays it would have waited are recorded instead"""
    delays: list[float] = []
    monkeypatch.setattr(RemoteJob, "_wait_unless_stopped", lambda self, secs: delays.append(secs))
    return delays


def _mock_health_check_ok(monkeypatch) -> None:
    """Mocks the GET /health pre-flight check that execute() makes before creating the job"""
    monkeypatch.setattr(httpx2, "get", lambda **kwargs: _FakeResponse({"status": "ok"}))


def _batch(n: int, rows: list, last: bool, columns: list[str] | None = None) -> tuple[int, bytes]:
    return 200, Batch(n, rows, last, columns).encode()


def _run(remote_server: RemoteServer, should_stop=lambda: False) -> tuple[tuple, list[tuple]]:
    """Columns and rows of an execution"""
    with remote_server.execute("Q1", "hash1", DisplayFormat(), {"@p": "1"}, should_stop=should_stop) as cursor:
        return tuple(col[0] for col in cursor.description), list(cursor)


@pytest.mark.unit
def test_execute_creates_a_job_waits_for_it_then_reads_its_batches(remote_server, jobs_server):
    jobs_server.script("GET", "/jobs/j1", (200, {"status": "running"}), (200, {"status": "ready"}))
    jobs_server.script(
        "GET", "/jobs/j1/batches/1", _batch(1, [[1, "a"], [2, "b"]], last=False, columns=["id", "name"])
    )
    jobs_server.script("GET", "/jobs/j1/batches/2", _batch(2, [[3, "c"]], last=True))

    columns, rows = _run(remote_server)

    assert columns == ("id", "name")
    assert rows == [(1, "a"), (2, "b"), (3, "c")]
    assert jobs_server.calls == [
        ("POST", "/jobs"),
        ("GET", "/jobs/j1"),
        ("GET", "/jobs/j1"),
        ("GET", "/jobs/j1/batches/1"),
        ("GET", "/jobs/j1/batches/2"),
        ("DELETE", "/jobs/j1"),  # acknowledges the last batch
    ]


@pytest.mark.unit
def test_execute_sends_the_query_and_its_parameters_to_create_the_job(remote_server, jobs_server):
    _run(remote_server)

    creation = jobs_server.requests[0]
    assert str(creation.url) == "http://127.0.0.1:8000/jobs"
    assert json.loads(creation.content) == {
        "server_id": "SRV1",
        "query_id": "Q1",
        "template_hash": "hash1",
        "display_format": DisplayFormat().to_dict(),
        "display_parameters": {"@p": "1"},
    }


@pytest.mark.unit
def test_execute_decodes_wrapped_datetime_date_and_decimal(remote_server, jobs_server):
    values = [datetime(2026, 1, 2, 3, 4, 5), date(2026, 1, 2), Decimal("12.50")]
    jobs_server.script("GET", "/jobs/j1/batches/1", _batch(1, [values], last=True, columns=["dt", "d", "dec"]))

    _columns, rows = _run(remote_server)

    assert rows == [tuple(values)]


@pytest.mark.unit
def test_execute_asks_again_for_a_batch_not_ready_yet(remote_server, jobs_server):
    jobs_server.script(
        "GET", "/jobs/j1/batches/1", (200, {"status": "pending"}), _batch(1, [[1]], last=True, columns=["id"])
    )

    _columns, rows = _run(remote_server)

    assert rows == [(1,)]
    assert jobs_server.calls.count(("GET", "/jobs/j1/batches/1")) == 2


@pytest.mark.unit
def test_execute_of_an_empty_result_has_its_columns_and_no_rows(remote_server, jobs_server):
    jobs_server.script("GET", "/jobs/j1/batches/1", _batch(1, [], last=True, columns=["id"]))

    assert _run(remote_server) == (("id",), [])


@pytest.mark.unit
@pytest.mark.parametrize("path", ["/jobs/j1", "/jobs/j1/batches/1"], ids=["execution", "batch"])
def test_execute_raises_remote_execution_error_on_an_error_status(remote_server, jobs_server, path):
    error = {"type": "AsyncSqlExecutionError", "message": "SQL error: Invalid object name"}
    jobs_server.script("GET", path, (200, {"status": "error", "error": error}))

    with pytest.raises(RemoteExecutionError, match="AsyncSqlExecutionError: SQL error: Invalid object name"):
        _run(remote_server)

    assert jobs_server.calls[-1] == ("DELETE", "/jobs/j1")


@pytest.mark.unit
def test_execute_raises_remote_execution_error_for_a_job_lost_by_the_server(remote_server, jobs_server):
    jobs_server.script("GET", "/jobs/j1", (404, {"detail": "Unknown job"}))

    with pytest.raises(RemoteExecutionError, match="Execution lost"):
        _run(remote_server)


@pytest.mark.unit
def test_execute_raises_remote_execution_error_for_a_response_that_isnt_a_job_one(remote_server, jobs_server):
    jobs_server.script("GET", "/jobs/j1", (200, b"<html>proxy page</html>"))

    with pytest.raises(RemoteExecutionError, match="Unexpected response"):
        _run(remote_server)


@pytest.mark.unit
def test_execute_raises_remote_http_status_error_when_the_creation_is_refused(remote_server, jobs_server):
    jobs_server.script("POST", "/jobs", (409, {"detail": "Query is not up to date, refresh your query list"}))

    with pytest.raises(RemoteHTTPStatusError, match="Query is not up to date"):
        _run(remote_server)

    assert jobs_server.calls == [("POST", "/jobs")]  # no job to delete


@pytest.mark.unit
def test_execute_never_retries_the_creation_after_a_network_error(remote_server, jobs_server, no_retry_wait):
    """The job may have been created and only its response lost: a retry would execute the query twice"""
    jobs_server.script("POST", "/jobs", httpx2.ConnectError("boom"))

    with pytest.raises(RemoteRequestError):
        _run(remote_server)

    assert jobs_server.calls == [("POST", "/jobs")]
    assert no_retry_wait == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "failure",
    [httpx2.ConnectError("boom"), (502, {"detail": "Bad gateway"}), (503, None), (504, None)],
    ids=["network", "502", "503", "504"],
)
def test_execute_retries_a_failed_request_with_a_growing_delay(remote_server, jobs_server, no_retry_wait, failure):
    jobs_server.script("GET", "/jobs/j1", failure, failure, (200, {"status": "ready"}))

    _run(remote_server)

    assert jobs_server.calls.count(("GET", "/jobs/j1")) == 3
    assert no_retry_wait == [1.0, 2.0]


@pytest.mark.unit
def test_execute_gives_up_once_the_retries_are_exhausted(remote_server, jobs_server, no_retry_wait, monkeypatch):
    monkeypatch.setattr(remotes_module, "_JOB_RETRY_SECS", 0)
    jobs_server.script("GET", "/jobs/j1", httpx2.ConnectError("boom"))

    with pytest.raises(RemoteRequestError, match="lost during the execution"):
        _run(remote_server)

    assert jobs_server.calls[-1] == ("DELETE", "/jobs/j1")  # tried anyway, the server abandons the job otherwise


@pytest.mark.unit
def test_execute_logs_in_again_on_a_401_then_sends_the_request_once_more(remote_server, jobs_server, auto_login_ok):
    """An access token expiring during a long execution"""
    jobs_server.script(
        "GET", "/jobs/j1", (401, {"detail": "Invalid or expired access token"}), (200, {"status": "ready"})
    )

    _run(remote_server)

    assert remote_server.address.access_token == "a1"
    assert jobs_server.requests[2].headers["Authorization"] == "Bearer a1"


@pytest.mark.unit
def test_execute_raises_remote_http_status_error_if_still_401_after_logging_in_again(
    remote_server, jobs_server, auto_login_ok
):
    jobs_server.script("GET", "/jobs/j1", (401, {"detail": "Invalid or expired access token"}))

    with pytest.raises(RemoteHTTPStatusError):
        _run(remote_server)

    assert jobs_server.calls.count(("GET", "/jobs/j1")) == 2


@pytest.mark.unit
def test_execute_raises_remote_auth_error_if_logging_in_again_fails(remote_server, jobs_server, monkeypatch):
    jobs_server.script("GET", "/jobs/j1", (401, {"detail": "Invalid or expired access token"}))
    error = httpx2.HTTPStatusError("401", request=None, response=None)  # type: ignore
    monkeypatch.setattr(httpx2, "post", lambda **kwargs: _FakeResponse(status_error=error))

    with pytest.raises(RemoteAuthError):
        _run(remote_server)


@pytest.mark.unit
def test_execute_stops_at_once_while_waiting_for_the_server(remote_server, jobs_server):
    """A status request can wait about 20 s on the server: a stop doesn't wait for it to end"""
    answering = threading.Event()
    release = threading.Event()

    def slow_status(_request):
        answering.set()
        release.wait(timeout=10)
        return 200, {"status": "running"}

    jobs_server.script("GET", "/jobs/j1", slow_status)
    started = time.monotonic()
    try:
        with pytest.raises(RemoteStopped):
            _run(remote_server, should_stop=answering.is_set)
    finally:
        release.set()

    assert time.monotonic() - started < 5
    assert jobs_server.calls[-1] == ("DELETE", "/jobs/j1")


@pytest.mark.unit
def test_execute_deletes_the_job_when_its_rows_are_left_unread(remote_server, jobs_server):
    jobs_server.script("GET", "/jobs/j1/batches/1", _batch(1, [[1], [2]], last=False, columns=["id"]))

    with remote_server.execute("Q1", "hash1", DisplayFormat(), {}) as cursor:
        next(iter(cursor))

    assert ("GET", "/jobs/j1/batches/2") not in jobs_server.calls
    assert jobs_server.calls[-1] == ("DELETE", "/jobs/j1")


@pytest.mark.unit
def test_execute_lets_errors_of_the_caller_through_unchanged(remote_server, jobs_server):
    with pytest.raises(ValueError, match="caller failure"):
        with remote_server.execute("Q1", "hash1", DisplayFormat(), {}):
            raise ValueError("caller failure")

    assert jobs_server.calls[-1] == ("DELETE", "/jobs/j1")


@pytest.mark.unit
def test_execute_ends_normally_even_if_the_job_can_not_be_deleted(remote_server, jobs_server):
    jobs_server.script("DELETE", "/jobs/j1", httpx2.ConnectError("boom"))

    assert _run(remote_server) == ((), [])


@pytest.mark.unit
def test_execute_checks_health_before_creating_the_job(remote_server, jobs_server, monkeypatch):
    captured = {}

    def fake_get(url, timeout=None):
        captured["url"] = url
        return _FakeResponse({"status": "ok"})

    monkeypatch.setattr(httpx2, "get", fake_get)

    _run(remote_server)

    assert captured["url"] == "http://127.0.0.1:8000/health"


@pytest.mark.unit
def test_execute_fails_fast_without_creating_a_job_if_health_check_fails(remote_server, jobs_server, monkeypatch):
    """If Pytre_Server itself is unreachable, execute() fails on the short /health timeout. Delegated to
    Remote.check_health() - see its own tests for the exception-wrapping details."""

    def fake_get(**kwargs):
        raise httpx2.ConnectError("boom")

    monkeypatch.setattr(httpx2, "get", fake_get)

    with pytest.raises(RemoteRequestError):
        _run(remote_server)

    assert jobs_server.calls == []


@pytest.mark.unit
def test_the_client_delays_fit_the_jobs_contract():
    """A response comes before the read timeout, and a request retried until the end of the client's retry window
    still finds the job: the server keeps it JOB_ABANDON_SECS without news"""
    assert remotes_module._JOB_READ_TIMEOUT > JOB_WAIT_SECS
    assert remotes_module._JOB_RETRY_SECS + JOB_WAIT_SECS < JOB_ABANDON_SECS


@pytest.mark.unit
def test_remote_job_waits_unless_stopped(remote_server):
    job = RemoteJob(remote_server.remote, should_stop=lambda: True)
    try:
        with pytest.raises(RemoteStopped):
            job._wait_unless_stopped(10)
    finally:
        job.__exit__(None, None, None)


@pytest.mark.unit
def test_remotes_default_to_the_file_of_the_user_config_folder():
    """Resolved when Remotes is built, not at import: follows the folder of the running platform"""
    assert Remotes().remotes_file == get_config_folder() / REMOTES_FILENAME


@pytest.mark.unit
def test_remotes_default_to_the_managed_file_of_the_install_folder(install_folder):
    assert Remotes().managed_file == install_folder / MANAGED_REMOTES_FILENAME


# ================================================================================
# Remotes deployed by the administrator
# ================================================================================


@pytest.fixture
def managed_file(install_folder) -> Any:
    """Pytre.remotes.json of the install folder, absent until a test writes it"""
    return install_folder / MANAGED_REMOTES_FILENAME


@pytest.fixture
def session_username(monkeypatch) -> str:
    username = "DOMAIN\\bob"
    monkeypatch.setattr(Users, "get_current_username", staticmethod(lambda: username))
    return username


def _write_managed_file(path, entries: dict, encoding: str = "utf-8") -> None:
    path.write_text(json.dumps(entries), encoding=encoding)


_DEPLOYED_A = {"url": "https://a.example.com", "port": 443, "description": "Remote A", "auth_method": "basic"}


@pytest.mark.unit
def test_remote_address_from_a_former_file_is_not_managed_and_asks_for_login():
    address = RemoteAddress.from_dict({"url": "http://a", "port": 8000, "username": "alice"})

    assert address.managed is False
    assert address.ask_for_login is True


@pytest.mark.unit
def test_remote_address_managed_and_ask_for_login_round_trip():
    address = RemoteAddress(url="http://a", port=8000, username="alice", managed=True, ask_for_login=False)

    rebuilt = RemoteAddress.from_dict(address.to_dict())

    assert rebuilt.managed is True
    assert rebuilt.ask_for_login is False


@pytest.mark.unit
def test_remote_address_base_id_ignores_case_and_a_trailing_slash():
    address = RemoteAddress(url="https://A.example.com/", port=443, username="Alice")

    assert address.base_id == "https://a.example.com:443"
    assert address.id == "alice@https://a.example.com:443"


@pytest.mark.unit
def test_login_without_password_raises_without_any_network_call(remotes, monkeypatch):
    """An empty login would count as a failed attempt for the server's sign-in throttling"""
    monkeypatch.setattr(httpx2, "post", lambda **kwargs: pytest.fail("no request expected"))
    remote = Remote(remotes, RemoteAddress(url="http://127.0.0.1", port=8000, username="alice"))

    with pytest.raises(RemoteAuthError, match="No password saved"):
        remote.login()

    assert isinstance(remote.last_error, RemoteAuthError)


@pytest.mark.unit
@pytest.mark.parametrize(
    "error, ask_for_login, expected",
    [
        (RemoteAuthError("refused"), True, True),
        (RemoteSsoRequiredError("expired"), True, True),
        (RemoteAuthError("refused"), False, False),
        (RemoteRequestError("unreachable"), True, False),
        (None, True, False),
    ],
)
def test_asking_for_login_selects_the_remotes_refused_for_lack_of_session(remotes, error, ask_for_login, expected):
    remote = Remote(remotes, RemoteAddress(url="http://a", port=8000, username="alice", ask_for_login=ask_for_login))
    remote.last_error = error
    remotes.remotes_dict = {remote.uuid: remote}

    assert (remotes.asking_for_login() == [remote]) is expected


@pytest.mark.unit
def test_sync_managed_adds_a_deployed_remote_with_the_session_username(remotes_file, managed_file, session_username):
    _write_managed_file(managed_file, {"prod": _DEPLOYED_A})

    remotes = Remotes(remotes_file)

    [remote] = remotes.remotes_dict.values()
    assert remote.managed is True
    assert remote.address.username == session_username
    assert remote.address.description == "Remote A"
    assert remote.address.ask_for_login is True
    assert [v["managed"] for v in _read_remotes_file(remotes_file).values()] == [True]  # saved at once


@pytest.mark.unit
@pytest.mark.parametrize("username, expected", [("carol", "carol"), ("", "DOMAIN\\bob")])
def test_sync_managed_uses_the_username_of_the_file_if_any(
    remotes_file, managed_file, session_username, username, expected
):
    _write_managed_file(managed_file, {"prod": _DEPLOYED_A | {"username": username}})

    [remote] = Remotes(remotes_file).remotes_dict.values()

    assert remote.address.username == expected


@pytest.mark.unit
def test_sync_managed_updates_the_fields_of_the_file_and_keeps_the_user_ones(remotes_file, managed_file):
    token = _make_token()
    address = RemoteAddress(
        url="https://a.example.com",
        port=443,
        username="alice",
        password="secret",
        refresh_token=token,
        description="Old",
        managed=True,
        ask_for_login=False,
    )
    _write_remotes_file(remotes_file, [address])
    _write_managed_file(managed_file, {"prod": _DEPLOYED_A | {"allow_unsecure_channel": True, "username": "carol"}})

    [remote] = Remotes(remotes_file).remotes_dict.values()

    assert remote.uuid == address.uuid
    assert (remote.address.description, remote.address.allow_unsecure_channel) == ("Remote A", True)
    assert (remote.address.username, remote.address.password) == ("alice", "secret")
    assert remote.address.refresh_token == token  # same server and auth method: the session is kept
    assert remote.address.ask_for_login is False


@pytest.mark.unit
def test_sync_managed_clears_the_tokens_when_the_auth_method_changes(remotes_file, managed_file):
    address = RemoteAddress(
        url="https://a.example.com", port=443, username="alice", refresh_token=_make_token(), managed=True
    )
    _write_remotes_file(remotes_file, [address])
    _write_managed_file(managed_file, {"prod": _DEPLOYED_A | {"auth_method": "oidc"}})

    [remote] = Remotes(remotes_file).remotes_dict.values()

    assert remote.uuid == address.uuid
    assert remote.address.auth_method is AuthType.OIDC
    assert remote.address.refresh_token == ""


@pytest.mark.unit
def test_sync_managed_replaces_a_remote_whose_port_changed(remotes_file, managed_file, session_username):
    """A remote is identified by its url and port: another port is another remote"""
    address = RemoteAddress(
        url="https://a.example.com", port=443, username="alice", refresh_token=_make_token(), managed=True
    )
    _write_remotes_file(remotes_file, [address])
    _write_managed_file(managed_file, {"prod": _DEPLOYED_A | {"port": 8443}})

    [remote] = Remotes(remotes_file).remotes_dict.values()

    assert remote.uuid != address.uuid
    assert remote.address.port == 8443
    assert (remote.address.username, remote.address.refresh_token) == (session_username, "")


@pytest.mark.unit
def test_sync_managed_adopts_the_user_remote_of_the_same_server(remotes_file, managed_file, session_username):
    address = RemoteAddress(url="https://A.example.com/", port=443, username="alice", password="secret")
    _write_remotes_file(remotes_file, [address])
    _write_managed_file(managed_file, {"prod": _DEPLOYED_A})

    [remote] = Remotes(remotes_file).remotes_dict.values()

    assert remote.uuid == address.uuid
    assert remote.managed is True
    assert (remote.address.username, remote.address.password) == ("alice", "secret")
    assert remote.address.url == "https://a.example.com"


@pytest.mark.unit
def test_sync_managed_adopts_the_user_remote_of_the_same_id_first(remotes_file, managed_file):
    other = RemoteAddress(url="https://a.example.com", port=443, username="admin")
    same = RemoteAddress(url="https://a.example.com", port=443, username="carol")
    _write_remotes_file(remotes_file, [other, same])
    _write_managed_file(managed_file, {"prod": _DEPLOYED_A | {"username": "carol"}})

    remotes = Remotes(remotes_file)

    assert {r.address.username: r.managed for r in remotes.remotes_dict.values()} == {"admin": False, "carol": True}


@pytest.mark.unit
def test_sync_managed_removes_the_remotes_no_longer_deployed(remotes_file, managed_file):
    gone = RemoteAddress(url="https://gone.example.com", port=443, username="alice", managed=True)
    own = RemoteAddress(url="https://own.example.com", port=443, username="alice")
    _write_remotes_file(remotes_file, [gone, own])
    _write_managed_file(managed_file, {"prod": _DEPLOYED_A})

    remotes = Remotes(remotes_file)

    assert sorted(r.address.url for r in remotes.remotes_dict.values()) == [
        "https://a.example.com",
        "https://own.example.com",
    ]
    assert gone.uuid not in _read_remotes_file(remotes_file)


@pytest.mark.unit
def test_sync_managed_removes_every_managed_remote_without_the_file(remotes_file):
    managed = RemoteAddress(url="https://a.example.com", port=443, username="alice", managed=True)
    own = RemoteAddress(url="https://own.example.com", port=443, username="alice")
    _write_remotes_file(remotes_file, [managed, own])

    remotes = Remotes(remotes_file)

    assert list(remotes.remotes_dict) == [own.uuid]


@pytest.mark.unit
@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        json.dumps([_DEPLOYED_A]),
        json.dumps({"prod": _DEPLOYED_A, "broken": {"url": "https://b.example.com"}}),
        json.dumps({"prod": _DEPLOYED_A, "broken": "https://b.example.com"}),
    ],
    ids=["invalid_json", "not_an_object", "entry_without_port", "entry_not_an_object"],
)
def test_sync_managed_leaves_the_managed_remotes_unchanged_on_an_invalid_file(
    remotes_file, managed_file, caplog, content
):
    managed = RemoteAddress(url="https://gone.example.com", port=443, username="alice", managed=True)
    _write_remotes_file(remotes_file, [managed])
    managed_file.write_text(content, encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        remotes = Remotes(remotes_file)

    assert list(remotes.remotes_dict) == [managed.uuid]
    assert any("deployed remotes left unchanged" in r.message for r in caplog.records)


@pytest.mark.unit
def test_sync_managed_never_reads_secrets_from_the_file(remotes_file, managed_file):
    """Values encrypted on another machine can't be decrypted: they must not even be read"""
    secrets = {"password": "clear", "encrypted_pwd": "not-a-fernet-token", "token": "not-a-fernet-token"}
    _write_managed_file(managed_file, {"prod": _DEPLOYED_A | secrets})

    [remote] = Remotes(remotes_file).remotes_dict.values()

    assert (remote.address.password, remote.address.refresh_token) == ("", "")


@pytest.mark.unit
def test_sync_managed_reads_a_file_with_a_byte_order_mark(remotes_file, managed_file):
    _write_managed_file(managed_file, {"prod": _DEPLOYED_A}, encoding="utf-8-sig")

    assert len(Remotes(remotes_file).remotes_dict) == 1


@pytest.mark.unit
def test_sync_managed_gives_a_new_uuid_to_a_key_already_used_by_a_user_remote(remotes_file, managed_file):
    own = RemoteAddress(url="https://own.example.com", port=443, username="alice")
    _write_remotes_file(remotes_file, [own])
    _write_managed_file(managed_file, {own.uuid: _DEPLOYED_A})

    remotes = Remotes(remotes_file)

    assert len(remotes.remotes_dict) == 2
    assert remotes.remotes_dict[own.uuid].address.url == "https://own.example.com"


@pytest.mark.unit
def test_sync_managed_saves_nothing_when_nothing_changed(remotes_file, managed_file, monkeypatch):
    _write_managed_file(managed_file, {"prod": _DEPLOYED_A})
    remotes = Remotes(remotes_file)
    monkeypatch.setattr(remotes, "save_remotes", lambda: pytest.fail("nothing to save"))

    assert remotes.sync_managed() is False


@pytest.mark.unit
def test_sync_managed_never_overwrites_a_user_file_that_could_not_be_fully_read(remotes_file, managed_file):
    """Saving it would lose the remotes that couldn't be read"""
    own = RemoteAddress(url="https://own.example.com", port=443, username="alice")
    _write_remotes_file(remotes_file, [own])
    data = _read_remotes_file(remotes_file) | {"broken": {"url": "https://b.example.com"}}
    remotes_file.write_text(json.dumps(data), encoding="utf-8")
    _write_managed_file(managed_file, {"prod": _DEPLOYED_A})

    remotes = Remotes(remotes_file)

    assert list(remotes.remotes_dict) == [own.uuid]  # the readable remotes are loaded
    assert _read_remotes_file(remotes_file) == data


@pytest.mark.unit
def test_csv_import_never_changes_a_managed_remote(tmp_path, remotes_file, managed_file, caplog):
    _write_managed_file(managed_file, {"prod": _DEPLOYED_A | {"username": "alice"}})
    remotes = Remotes(remotes_file)
    csv_file = tmp_path / "remotes.csv"
    csv_file.write_text(
        "url;port;username;description\nhttps://a.example.com;443;alice;Changed\n", encoding="utf-8-sig"
    )

    with caplog.at_level(logging.WARNING):
        remotes.csv_import(csv_file)

    [remote] = remotes.remotes_dict.values()
    assert (remote.managed, remote.address.description) == (True, "Remote A")
    assert any("is deployed by your administrator" in r.message for r in caplog.records)


@pytest.mark.unit
def test_csv_import_never_creates_a_managed_remote(tmp_path, remotes_file):
    remotes = Remotes(remotes_file)
    csv_file = tmp_path / "remotes.csv"
    csv_file.write_text("url;port;username;description;managed\nhttp://a;8000;alice;A;true\n", encoding="utf-8-sig")

    remotes.csv_import(csv_file)

    [remote] = remotes.remotes_dict.values()
    assert remote.managed is False


@pytest.mark.unit
def test_delete_refuses_a_managed_remote(remotes_file, managed_file):
    _write_managed_file(managed_file, {"prod": _DEPLOYED_A})
    remotes = Remotes(remotes_file)
    [remote] = remotes.remotes_dict.values()

    with pytest.raises(ValueError, match="deployed by your administrator"):
        remote.delete()

    assert remote.uuid in _read_remotes_file(remotes_file)
