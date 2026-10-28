"""
Tests for Pytre_Core/remotes.py
"""

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx2
import jwt
import pytest

from Pytre_Core.remotes import (
    REMOTES_CIPHER,
    Remote,
    RemoteAddress,
    RemoteAuthError,
    RemoteError,
    RemoteExecutionError,
    RemoteHTTPStatusError,
    RemoteInsecureChannelError,
    RemoteRequestError,
    RemoteServer,
    RemoteSsoRequiredError,
    Remotes,
    decode_row_value,
    detail_from_response,
    encode_row_value,
)
from Pytre_Core.users import AuthType
from Pytre_Core.user_prefs import DisplayFormat


# ================================================================================
# encode_row_value / decode_row_value
# ================================================================================


@pytest.mark.unit
def test_encode_row_value_wraps_datetime_date_and_decimal():
    assert encode_row_value(datetime(2026, 1, 1, 10, 30)) == {"$datetime": "2026-01-01T10:30:00"}
    assert encode_row_value(date(2026, 1, 1)) == {"$date": "2026-01-01"}
    assert encode_row_value(Decimal("12.50")) == {"$decimal": "12.50"}


@pytest.mark.unit
def test_encode_row_value_passes_through_json_native_types():
    assert encode_row_value("hello") == "hello"
    assert encode_row_value(3) == 3
    assert encode_row_value(1.5) == 1.5
    assert encode_row_value(True) is True
    assert encode_row_value(None) is None


@pytest.mark.unit
def test_decode_row_value_unwraps_datetime_date_and_decimal():
    assert decode_row_value({"$datetime": "2026-01-01T10:30:00"}) == datetime(2026, 1, 1, 10, 30)
    assert decode_row_value({"$date": "2026-01-01"}) == date(2026, 1, 1)
    assert decode_row_value({"$decimal": "12.50"}) == Decimal("12.50")


@pytest.mark.unit
def test_decode_row_value_passes_through_unwrapped_values():
    assert decode_row_value("hello") == "hello"
    assert decode_row_value(3) == 3
    assert decode_row_value(None) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "value", [datetime(2026, 1, 1, 10, 30), date(2026, 1, 1), Decimal("12.50"), "hello", 3, 1.5, True, None]
)
def test_encode_decode_round_trip(value):
    assert decode_row_value(encode_row_value(value)) == value


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


@pytest.fixture
def auto_login_ok(monkeypatch):
    """Makes any Remote.login() call succeed via a full /login round trip, without caring
    about the two-tier refresh/login mechanics themselves (covered separately near
    Remote.login()) - lets tests focus on what happens once a Remote is authenticated."""
    monkeypatch.setattr(httpx2, "post", lambda **kwargs: _FakeResponse({"refresh_token": "r1", "access_token": "a1"}))


@pytest.fixture
def address() -> RemoteAddress:
    """Loopback host so login()'s HTTPS-or-local gate doesn't get in the way of tests that
    aren't specifically about that gate - see the dedicated tests near Remote.login()."""
    return RemoteAddress(url="http://127.0.0.1", port=8000, username="alice")


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
    """The explicit opt-out (e.g. an enterprise/Azure network not detected as private) bypasses
    the channel_is_secure gate entirely - login() proceeds normally."""
    address.url = "http://8.8.8.8"
    address.allow_unsecure_channel = True
    monkeypatch.setattr(httpx2, "post", lambda **kwargs: _FakeResponse({"refresh_token": "r1", "access_token": "a1"}))

    result = Remote(remotes, address).login()

    assert result is True


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
    addr1 = RemoteAddress(url="http://127.0.0.1", port=8000, username="alice")
    addr2 = RemoteAddress(url="http://127.0.0.1", port=8001, username="bob")
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
    addr = RemoteAddress(url="http://127.0.0.1", port=8000, username="alice")
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
    assert saved == {address.uuid: {k: v for k, v in address.to_dict().items() if k != "uuid"}}


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

    assert data == {
        "id_on_remote": "SRV1",
        "description": "Server 1",
        "address": address.to_dict(),
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
# RemoteServer.execute
# ================================================================================


class _FakeStreamResponse:
    """Minimal stand-in for the Response yielded by httpx2.stream() - only what
    RemoteServer.execute() actually uses."""

    def __init__(self, lines: list[str], status_code: int = 200, status_error: Exception | None = None):
        self._lines = lines
        self.status_code = status_code
        self._status_error = status_error

    def raise_for_status(self) -> None:
        if self._status_error is not None:
            raise self._status_error

    def read(self) -> None:
        """No-op: execute() calls this to buffer the body before re-raising on a status
        error, so a later e.response.json()/.text access doesn't hit ResponseNotRead."""
        pass

    def iter_lines(self):
        return iter(self._lines)


class _FakeStreamContext:
    """Minimal stand-in for the context manager httpx2.stream() returns."""

    def __init__(self, response: _FakeStreamResponse):
        self._response = response

    def __enter__(self) -> _FakeStreamResponse:
        return self._response

    def __exit__(self, *exc_info) -> bool:
        return False


def _ndjson_lines(*payloads: dict) -> list[str]:
    return [json.dumps(p) for p in payloads]


def _mock_health_check_ok(monkeypatch) -> None:
    """Mocks the GET /health pre-flight check that execute() makes before streaming -
    without this, httpx2.get() would attempt a real network call in every test below."""
    monkeypatch.setattr(httpx2, "get", lambda **kwargs: _FakeResponse({"status": "ok"}))


@pytest.mark.unit
def test_execute_yields_columns_and_rows(remote_server, monkeypatch):
    _mock_health_check_ok(monkeypatch)
    lines = _ndjson_lines({"__columns__": ["id", "name"]}, {"__row__": ["1", "a"]}, {"__row__": ["2", "b"]})
    monkeypatch.setattr(httpx2, "stream", lambda *a, **k: _FakeStreamContext(_FakeStreamResponse(lines)))

    with remote_server.execute("Q1", "hash1", DisplayFormat(), {}) as rows:
        assert [col[0] for col in rows.description] == ["id", "name"]
        assert list(rows) == [("1", "a"), ("2", "b")]


@pytest.mark.unit
def test_execute_skips_pings_before_columns_and_between_rows(remote_server, monkeypatch):
    """The server sends pings while the query executes, and while it waits for the next rows."""
    _mock_health_check_ok(monkeypatch)
    ping = {"__ping__": True}
    lines = _ndjson_lines(
        ping, ping, {"__columns__": ["id"]}, ping, {"__row__": ["1"]}, ping, ping, {"__row__": ["2"]}, ping
    )
    monkeypatch.setattr(httpx2, "stream", lambda *a, **k: _FakeStreamContext(_FakeStreamResponse(lines)))

    with remote_server.execute("Q1", "hash1", DisplayFormat(), {}) as rows:
        assert [col[0] for col in rows.description] == ["id"]
        assert list(rows) == [("1",), ("2",)]


@pytest.mark.unit
def test_execute_decodes_wrapped_datetime_date_and_decimal(remote_server, monkeypatch):
    """Mirrors Pytre_Server's row encoding - the client must reconstruct real Python types
    so its own SqlTypeConverter settings apply."""
    _mock_health_check_ok(monkeypatch)
    lines = _ndjson_lines(
        {"__columns__": ["created", "day", "amount", "label"]},
        {
            "__row__": [
                {"$datetime": "2026-01-01T10:30:00"},
                {"$date": "2026-01-01"},
                {"$decimal": "12.50"},
                "unwrapped",
            ]
        },
    )
    monkeypatch.setattr(httpx2, "stream", lambda *a, **k: _FakeStreamContext(_FakeStreamResponse(lines)))

    with remote_server.execute("Q1", "hash1", DisplayFormat(), {}) as rows:
        result = list(rows)

    assert result == [(datetime(2026, 1, 1, 10, 30), date(2026, 1, 1), Decimal("12.50"), "unwrapped")]


@pytest.mark.unit
def test_execute_sends_correct_request(remote_server, monkeypatch):
    _mock_health_check_ok(monkeypatch)
    captured = {}
    lines = _ndjson_lines({"__columns__": []})

    def fake_stream(method, url, json=None, headers=None, timeout=None):
        captured["method"] = method
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeStreamContext(_FakeStreamResponse(lines))

    monkeypatch.setattr(httpx2, "stream", fake_stream)

    with remote_server.execute("Q1", "hash1", DisplayFormat(), {"p": "v"}) as rows:
        list(rows)

    assert captured["method"] == "POST"
    assert captured["url"] == "http://127.0.0.1:8000/execute"
    assert captured["json"] == {
        "server_id": "SRV1",
        "query_id": "Q1",
        "template_hash": "hash1",
        "display_format": {"date_format": "%d/%m/%Y", "decimal_separator": ","},
        "display_parameters": {"p": "v"},
    }
    assert captured["headers"] == {}  # no token yet - no Authorization header, no username either


@pytest.mark.unit
def test_execute_empty_result_has_no_columns_or_rows(remote_server, monkeypatch):
    _mock_health_check_ok(monkeypatch)
    lines = _ndjson_lines({"__columns__": []})
    monkeypatch.setattr(httpx2, "stream", lambda *a, **k: _FakeStreamContext(_FakeStreamResponse(lines)))

    with remote_server.execute("Q1", "hash1", DisplayFormat(), {}) as rows:
        assert rows.description == ()
        assert list(rows) == []


@pytest.mark.unit
def test_execute_raises_remote_execution_error_on_error_line(remote_server, monkeypatch):
    _mock_health_check_ok(monkeypatch)
    lines = _ndjson_lines(
        {"__columns__": ["id"]},
        {"__row__": ["1"]},
        {"__error__": {"type": "ValueError", "message": "boom"}},
    )
    monkeypatch.setattr(httpx2, "stream", lambda *a, **k: _FakeStreamContext(_FakeStreamResponse(lines)))

    collected = []
    with remote_server.execute("Q1", "hash1", DisplayFormat(), {}) as rows:
        with pytest.raises(RemoteExecutionError, match="ValueError: boom"):
            for row in rows:
                collected.append(row)

    assert collected == [("1",)]  # the row read before the error line is not lost


@pytest.mark.unit
def test_execute_wraps_http_status_error_as_remote_http_status_error(remote_server, monkeypatch):
    _mock_health_check_ok(monkeypatch)
    error = httpx2.HTTPStatusError("404", request=None, response=None)  # type: ignore
    monkeypatch.setattr(
        httpx2, "stream", lambda *a, **k: _FakeStreamContext(_FakeStreamResponse([], status_error=error))
    )

    with pytest.raises(RemoteHTTPStatusError):
        with remote_server.execute("Q1", "hash1", DisplayFormat(), {}):
            pass


@pytest.mark.unit
def test_execute_checks_health_before_streaming(remote_server, monkeypatch):
    """The GET /health pre-flight must hit the right URL, before the POST /execute stream."""
    captured = {}
    lines = _ndjson_lines({"__columns__": []})

    def fake_get(url, timeout=None):
        captured["url"] = url
        return _FakeResponse({"status": "ok"})

    monkeypatch.setattr(httpx2, "get", fake_get)
    monkeypatch.setattr(httpx2, "stream", lambda *a, **k: _FakeStreamContext(_FakeStreamResponse(lines)))

    with remote_server.execute("Q1", "hash1", DisplayFormat(), {}) as rows:
        list(rows)

    assert captured["url"] == "http://127.0.0.1:8000/health"


@pytest.mark.unit
def test_execute_fails_fast_without_streaming_if_health_check_fails(remote_server, monkeypatch):
    """Regression: if Pytre_Server itself is unreachable, execute() must fail via the short
    /health timeout instead of ever attempting the long-timeout POST /execute stream. Delegated
    to Remote.check_health() - see its own dedicated tests for the exception-wrapping details."""

    def fake_get(**kwargs):
        raise httpx2.ConnectError("boom")

    monkeypatch.setattr(httpx2, "get", fake_get)
    monkeypatch.setattr(httpx2, "stream", lambda *a, **k: pytest.fail("httpx2.stream should not be called"))

    with pytest.raises(RemoteRequestError):
        with remote_server.execute("Q1", "hash1", DisplayFormat(), {}):
            pass


@pytest.mark.unit
def test_execute_relogs_in_and_retries_once_on_401(remote_server, monkeypatch):
    """No upfront token check: a missing/expired token surfaces as a 401 on the first POST
    /execute attempt, handled by the same retry-and-relogin path as a later invalidation."""
    _mock_health_check_ok(monkeypatch)
    success_lines = _ndjson_lines({"__columns__": ["id"]}, {"__row__": ["1"]})
    stream_contexts = iter(
        [
            _FakeStreamContext(_FakeStreamResponse([], status_code=401)),
            _FakeStreamContext(_FakeStreamResponse(success_lines, status_code=200)),
        ]
    )
    monkeypatch.setattr(httpx2, "stream", lambda *a, **k: next(stream_contexts))
    monkeypatch.setattr(
        httpx2, "post", lambda **kwargs: _FakeResponse({"refresh_token": "r1", "access_token": "new-token"})
    )

    with remote_server.execute("Q1", "hash1", DisplayFormat(), {}) as rows:
        result = list(rows)

    assert result == [("1",)]
    assert remote_server.address.access_token == "new-token"


@pytest.mark.unit
def test_execute_raises_remote_http_status_error_if_still_401_after_relogin(remote_server, monkeypatch):
    """Relogin succeeded (new token issued) but the retried request is still rejected - the
    2nd attempt is the last one (max_login_retry reached), so it must surface as-is, wrapped
    the same way as any other non-retriable HTTP failure."""
    _mock_health_check_ok(monkeypatch)
    error = httpx2.HTTPStatusError("401", request=None, response=None)  # type: ignore
    stream_contexts = iter(
        [
            _FakeStreamContext(_FakeStreamResponse([], status_code=401)),
            _FakeStreamContext(_FakeStreamResponse([], status_code=401, status_error=error)),
        ]
    )
    monkeypatch.setattr(httpx2, "stream", lambda *a, **k: next(stream_contexts))
    monkeypatch.setattr(
        httpx2, "post", lambda **kwargs: _FakeResponse({"refresh_token": "r1", "access_token": "new-token"})
    )

    with pytest.raises(RemoteHTTPStatusError):
        with remote_server.execute("Q1", "hash1", DisplayFormat(), {}):
            pass


@pytest.mark.unit
def test_execute_raises_remote_auth_error_if_relogin_fails_with_bad_credentials(remote_server, monkeypatch):
    """Login itself fails because credentials are no longer valid (e.g. stored password
    changed server-side) - propagates as RemoteAuthError, distinct from a network failure or a
    plain HTTP failure on /execute itself, so callers can offer to re-enter credentials."""
    _mock_health_check_ok(monkeypatch)
    monkeypatch.setattr(
        httpx2, "stream", lambda *a, **k: _FakeStreamContext(_FakeStreamResponse([], status_code=401))
    )
    error = httpx2.HTTPStatusError("401", request=None, response=None)  # type: ignore
    monkeypatch.setattr(httpx2, "post", lambda **kwargs: _FakeResponse(status_error=error))

    with pytest.raises(RemoteAuthError):
        with remote_server.execute("Q1", "hash1", DisplayFormat(), {}):
            pass


@pytest.mark.unit
def test_execute_raises_remote_request_error_if_relogin_fails_due_to_network(remote_server, monkeypatch):
    """Login itself fails due to a network error while retrying after a 401 - propagates as
    RemoteRequestError, distinct from the bad-credentials case above."""
    _mock_health_check_ok(monkeypatch)
    monkeypatch.setattr(
        httpx2, "stream", lambda *a, **k: _FakeStreamContext(_FakeStreamResponse([], status_code=401))
    )

    def fake_post(**kwargs):
        raise httpx2.ConnectError("boom")

    monkeypatch.setattr(httpx2, "post", fake_post)

    with pytest.raises(RemoteRequestError):
        with remote_server.execute("Q1", "hash1", DisplayFormat(), {}):
            pass
