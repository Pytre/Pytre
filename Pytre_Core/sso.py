"""
Client-side helpers for mediated-mode SSO login: PKCE/state generation
(reusing Authlib and Pytre_Core.oidc_client.new_token() - no custom crypto code), and a
short-lived loopback HTTP listener that catches the browser redirect Server sends back
after a successful IdP login.
"""

import http.server
import time
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from urllib.parse import parse_qs, urlencode, urlparse
from typing import cast, Any

import httpx2
from authlib.oauth2.rfc7636 import create_s256_code_challenge

from Pytre_Core.about import APP_NAME
from Pytre_Core.oidc_client import new_token


class SsoLoginError(Exception):
    """Raised for any failure of the mediated-mode login flow - a timeout waiting for the
    browser, a state mismatch, an error reported by the server/IdP, or a failed token exchange."""


class SsoLoginCancelled(SsoLoginError):
    """Raised when the login was interrupted by a call to LoopbackListener.shutdown() (e.g. the
    user clicked "Cancel") rather than by an actual failure."""


@dataclass
class LoopbackResult:
    """What the browser's redirect back to the loopback listener carried."""

    code: str | None = None
    state: str | None = None
    error: str | None = None
    cancelled: bool = False


class _CallbackServer(http.server.HTTPServer):
    """HTTPServer subclass with a typed slot for the result the handler extracts."""

    result: LoopbackResult | None = None

    def handle_request(self) -> None:
        """Reset the captured result then handles one request (possibly blocking, respecting self.timeout)."""
        self.result = None
        super().handle_request()


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Handles the single real request the loopback listener cares about (the OIDC redirect),
    ignoring anything else a browser might also send (e.g. a speculative /favicon.ico fetch) so
    that request doesn't get mistaken for the real callback."""

    def do_GET(self) -> None:
        server = cast(_CallbackServer, self.server)  # for typehint
        parsed = urlparse(self.path)

        if parsed.path == "/__cancel__":
            server.result = LoopbackResult(cancelled=True)
            self._respond(b"")
            return

        if parsed.path != "/":
            self._respond(b"", status=HTTPStatus.NO_CONTENT)  # e.g. /favicon.ico - not the callback, keep waiting
            return

        params = parse_qs(parsed.query)
        server.result = LoopbackResult(
            code=params.get("code", [None])[0],
            state=params.get("state", [None])[0],
            error=params.get("error", [None])[0],
        )
        html_content = f"<html><body>You can close this window and return to {APP_NAME}.</body></html>"
        self._respond(html_content.encode())

    def _respond(self, body: bytes, status: int = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass  # silence BaseHTTPRequestHandler's default stderr logging


class LoopbackListener:
    """A short-lived HTTP server on 127.0.0.1 (OS-assigned port) that waits for exactly one real
    browser redirect and parses its query string. shutdown() is meant to be called from a
    different thread than the one blocked in wait_for_result()."""

    def __init__(self, timeout_secs: float = 180) -> None:
        self._callback_server = _CallbackServer(("127.0.0.1", 0), _CallbackHandler)
        self._timeout_secs = timeout_secs

    @property
    def port(self) -> int:
        return self._callback_server.server_address[1]

    def wait_for_result(self) -> LoopbackResult:
        """Blocks until the browser hits the callback, shutdown() is called, or `timeout_secs`
        elapses. Always closes the listening socket before returning or raising."""
        deadline = time.monotonic() + self._timeout_secs
        result: LoopbackResult | None = None
        try:
            while result is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SsoLoginError("Timed out waiting for the browser redirect")

                self._callback_server.timeout = remaining
                self._callback_server.handle_request()
                result = self._callback_server.result
        finally:
            self._callback_server.server_close()

        if result.cancelled:
            raise SsoLoginCancelled("Login cancelled")

        return result

    def shutdown(self) -> None:
        """Unblocks a pending wait_for_result() from another thread, as a cancellation - connects
        to the listener's own port with a marker path rather than closing the socket out from
        under a blocked accept() (a known race on some platforms)."""
        try:
            httpx2.get(url=f"http://127.0.0.1:{self.port}/__cancel__", timeout=5)
        except httpx2.RequestError:
            pass  # already closed/timed out on its own - nothing left to cancel


def login_via_browser(
    server_url: str,
    open_browser: Callable[[str], Any],
    timeout_secs: float = 180,
    on_listener_ready: Callable[[LoopbackListener], None] | None = None,
) -> tuple[str, str]:
    """Orchestrates one full mediated login: starts a loopback listener, builds the
    /login/sso/start URL, calls open_browser(url), blocks on the loopback redirect, then
    exchanges the resulting code for a Pytre token pair via /login/sso/token.
    Returns (refresh_token, access_token). Raises SsoLoginError/SsoLoginCancelled on failure."""
    state = new_token()
    code_verifier = new_token()
    code_challenge = create_s256_code_challenge(code_verifier)
    build_url = lambda route: server_url.rstrip("/") + route

    listener = LoopbackListener(timeout_secs=timeout_secs)
    if on_listener_ready is not None:
        on_listener_ready(listener)

    params = {"port": listener.port, "state": state, "code_challenge": code_challenge}
    start_url = build_url(f"/login/sso/start?{urlencode(params)}")
    open_browser(start_url)

    result = listener.wait_for_result()

    if result.error:
        raise SsoLoginError(f"Login failed: {result.error}")
    if result.state != state:
        raise SsoLoginError("State mismatch - the response did not come from the started login")
    if not result.code:
        raise SsoLoginError("No code received from the server")

    try:
        response = httpx2.post(
            url=build_url(f"/login/sso/token"),
            json={"code": result.code, "code_verifier": code_verifier},
            timeout=10,
        )
        response.raise_for_status()
    except (httpx2.HTTPStatusError, httpx2.RequestError) as e:
        raise SsoLoginError(f"Could not exchange the code for tokens: {e}") from e

    data = response.json()

    return data["refresh_token"], data["access_token"]
