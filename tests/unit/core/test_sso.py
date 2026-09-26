"""
Tests for Pytre_Core/sso.py - Pytre_Client's mediated-mode SSO login: the loopback HTTP listener
(LoopbackListener) and the full login orchestration (login_via_browser()).

create_s256_code_challenge()/new_token() aren't re-tested here - they're Authlib's and the
project's own already-proven functions (new_token() is covered by test_oidc_client.py) - these
tests focus on what's actually new: the loopback listener's request handling/timeout/cancellation,
and login_via_browser()'s orchestration logic.
"""

import threading
import time
from urllib.parse import parse_qs, urlparse

import httpx2
import pytest
from authlib.oauth2.rfc7636 import create_s256_code_challenge

from Pytre_Core.sso import LoopbackListener, SsoLoginCancelled, SsoLoginError, login_via_browser

pytestmark = pytest.mark.unit


class _FakeResponse:
    """Minimal stand-in for httpx2.Response - only what login_via_browser() actually uses."""

    def __init__(self, json_data=None, status_error: Exception | None = None):
        self._json_data = json_data
        self._status_error = status_error

    def raise_for_status(self) -> None:
        if self._status_error is not None:
            raise self._status_error

    def json(self):
        return self._json_data


def _fire_loopback_request(port: int, **params) -> None:
    """Hits the loopback listener's own port with the given query params, from a background
    thread - simulates the browser's redirect back after the server/IdP finished. Must run in a
    separate thread: the full request/response round trip only completes once the listener's own
    wait_for_result() processes it, so firing it synchronously here would deadlock."""

    def fire():
        httpx2.get(url=f"http://127.0.0.1:{port}/", params=params, timeout=5)

    threading.Thread(target=fire, daemon=True).start()


def _open_browser_that_calls_back(**params):
    """Returns an open_browser(url) callable that extracts port/state from the /login/sso/start
    URL it's given and immediately fires a loopback request carrying `params` (plus that same
    state, unless overridden) - simulates a successful (or failed) browser round trip without a
    real browser or network."""

    def open_browser(url: str) -> None:
        query = parse_qs(urlparse(url).query)
        port = int(query["port"][0])
        state = query["state"][0]
        final_params = {"state": state, **params}
        _fire_loopback_request(port, **final_params)

    return open_browser


# ================================================================================
# LoopbackListener
# ================================================================================


def test_loopback_listener_captures_code_and_state_from_a_real_request():
    listener = LoopbackListener(timeout_secs=5)
    _fire_loopback_request(listener.port, code="abc123", state="xyz789")

    result = listener.wait_for_result()

    assert result.code == "abc123"
    assert result.state == "xyz789"
    assert result.error is None
    assert result.cancelled is False


def test_loopback_listener_captures_an_error_param():
    listener = LoopbackListener(timeout_secs=5)
    _fire_loopback_request(listener.port, error="access_denied", state="xyz789")

    result = listener.wait_for_result()

    assert result.error == "access_denied"
    assert result.code is None


def test_loopback_listener_ignores_a_favicon_request_and_waits_for_the_real_one():
    """A browser hitting a bare URL may speculatively fetch /favicon.ico - that request must not
    be mistaken for the actual OIDC callback, or the real one would never get a chance to land."""
    listener = LoopbackListener(timeout_secs=5)

    def fire():
        httpx2.get(url=f"http://127.0.0.1:{listener.port}/favicon.ico", timeout=5)
        time.sleep(0.05)
        httpx2.get(url=f"http://127.0.0.1:{listener.port}/", params={"code": "real", "state": "s1"}, timeout=5)

    threading.Thread(target=fire, daemon=True).start()

    result = listener.wait_for_result()

    assert result.code == "real"


def test_loopback_listener_times_out_if_nothing_connects():
    listener = LoopbackListener(timeout_secs=0.3)

    with pytest.raises(SsoLoginError, match="Timed out"):
        listener.wait_for_result()


def test_loopback_listener_shutdown_from_another_thread_cancels_promptly():
    listener = LoopbackListener(timeout_secs=30)

    def cancel_soon():
        time.sleep(0.1)
        listener.shutdown()

    threading.Thread(target=cancel_soon, daemon=True).start()

    start = time.monotonic()
    with pytest.raises(SsoLoginCancelled):
        listener.wait_for_result()

    # proves shutdown() actually interrupted the wait rather than the 30s timeout elapsing on its own
    assert time.monotonic() - start < 5


def test_loopback_listener_shutdown_after_completion_does_not_raise():
    """A late/duplicate cancel click (shutdown() called after wait_for_result() already returned)
    must be a harmless no-op - the listener's socket is already closed by then."""
    listener = LoopbackListener(timeout_secs=5)
    _fire_loopback_request(listener.port, code="abc", state="s1")
    listener.wait_for_result()

    listener.shutdown()  # must not raise


# ================================================================================
# login_via_browser()
# ================================================================================


def test_login_via_browser_full_round_trip(monkeypatch):
    captured_post = {}

    def fake_post(url, json, timeout):
        captured_post["url"] = url
        captured_post["json"] = json
        return _FakeResponse({"refresh_token": "rt-abc", "access_token": "at-abc"})

    monkeypatch.setattr(httpx2, "post", fake_post)

    refresh_token, access_token = login_via_browser(
        "http://pytre.example.com", _open_browser_that_calls_back(code="the-code"), timeout_secs=5
    )

    assert refresh_token == "rt-abc"
    assert access_token == "at-abc"
    assert captured_post["url"] == "http://pytre.example.com/login/sso/token"
    assert captured_post["json"]["code"] == "the-code"


def test_login_via_browser_sends_a_code_verifier_matching_the_challenge_in_the_start_url(monkeypatch):
    """The PKCE pair must be internally consistent - whatever challenge went out in the
    /login/sso/start URL must be derivable from the verifier sent to /login/sso/token."""
    captured = {}

    def open_browser(url: str) -> None:
        query = parse_qs(urlparse(url).query)
        captured["code_challenge"] = query["code_challenge"][0]
        _fire_loopback_request(int(query["port"][0]), code="c1", state=query["state"][0])

    def fake_post(url, json, timeout):
        captured["code_verifier"] = json["code_verifier"]
        return _FakeResponse({"refresh_token": "rt", "access_token": "at"})

    monkeypatch.setattr(httpx2, "post", fake_post)

    login_via_browser("http://pytre.example.com", open_browser, timeout_secs=5)

    assert create_s256_code_challenge(captured["code_verifier"]) == captured["code_challenge"]


def test_login_via_browser_rejects_a_state_mismatch():
    """A response carrying a different state than the one we generated must never be trusted -
    whether due to a bug, a stale/replayed request, or a forged callback."""

    def open_browser(url: str) -> None:
        port = int(parse_qs(urlparse(url).query)["port"][0])
        _fire_loopback_request(port, code="c1", state="not-the-real-state")

    with pytest.raises(SsoLoginError, match="State mismatch"):
        login_via_browser("http://pytre.example.com", open_browser, timeout_secs=5)


def test_login_via_browser_propagates_an_error_from_the_server_or_idp():
    with pytest.raises(SsoLoginError, match="access_denied"):
        login_via_browser(
            "http://pytre.example.com", _open_browser_that_calls_back(error="access_denied"), timeout_secs=5
        )


def test_login_via_browser_rejects_a_missing_code():
    """A callback that isn't an error but also doesn't carry a code (shouldn't normally happen
    server-side, but must still be handled defensively) must not proceed to the token exchange."""

    def open_browser(url: str) -> None:
        query = parse_qs(urlparse(url).query)
        _fire_loopback_request(int(query["port"][0]), state=query["state"][0])  # no code=

    with pytest.raises(SsoLoginError, match="No code received"):
        login_via_browser("http://pytre.example.com", open_browser, timeout_secs=5)


def test_login_via_browser_wraps_a_token_exchange_http_error(monkeypatch):
    def fake_post(url, json, timeout):
        raise httpx2.HTTPStatusError("401 Unauthorized", request=None, response=None)  # type: ignore

    monkeypatch.setattr(httpx2, "post", fake_post)

    with pytest.raises(SsoLoginError, match="Could not exchange"):
        login_via_browser("http://pytre.example.com", _open_browser_that_calls_back(code="c1"), timeout_secs=5)


def test_login_via_browser_calls_on_listener_ready_before_opening_the_browser(monkeypatch):
    """on_listener_ready() is the hook a caller (the Tkinter wrapper) uses to grab a reference to
    the listener before the blocking wait, e.g. to wire up a "Cancel" button's shutdown() call -
    it must fire before the browser opens, so a near-instant cancel still has something to call."""
    call_order = []

    def on_listener_ready(listener):
        call_order.append(listener)

    def open_browser(url: str) -> None:
        call_order.append("open_browser")
        query = parse_qs(urlparse(url).query)
        _fire_loopback_request(int(query["port"][0]), code="c1", state=query["state"][0])

    monkeypatch.setattr(
        httpx2, "post", lambda url, json, timeout: _FakeResponse({"refresh_token": "rt", "access_token": "at"})
    )

    login_via_browser("http://pytre.example.com", open_browser, timeout_secs=5, on_listener_ready=on_listener_ready)

    assert isinstance(call_order[0], LoopbackListener)
    assert call_order[1] == "open_browser"


def test_login_via_browser_handles_a_trailing_slash_in_server_url(monkeypatch):
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        return _FakeResponse({"refresh_token": "rt", "access_token": "at"})

    monkeypatch.setattr(httpx2, "post", fake_post)

    login_via_browser("http://pytre.example.com/", _open_browser_that_calls_back(code="c1"), timeout_secs=5)

    assert captured["url"] == "http://pytre.example.com/login/sso/token"
