"""
Tests for Pytre_Client/sso_login.py - a SSO sign-in follows the same channel rule as a password login.
"""

from unittest.mock import Mock

import pytest

from Pytre_Client import sso_login
from Pytre_Core.remotes import Remote, RemoteAddress

pytestmark = pytest.mark.unit


@pytest.fixture
def sso_spies(monkeypatch) -> dict[str, Mock]:
    """Everything prompt_sso_login() would show or start, replaced by mocks."""
    spies = {
        "showerror": Mock(),
        "dialog": Mock(),
        "thread": Mock(),
    }
    monkeypatch.setattr(sso_login.messagebox, "showerror", spies["showerror"])
    monkeypatch.setattr(sso_login, "SsoWaitDialog", spies["dialog"])
    monkeypatch.setattr(sso_login, "Thread", spies["thread"])
    return spies


def test_sso_login_is_refused_before_opening_the_browser_when_plain_http_is_disabled(sso_spies, monkeypatch):
    """A SSO sign-in trades a code for tokens: over plain HTTP to another machine, it must not even start."""
    monkeypatch.setattr("Pytre_Core.remotes.plain_http_allowed", lambda: False)
    remote = Remote(None, RemoteAddress(url="http://10.0.0.5", port=8000, username="alice"))
    on_done = Mock()

    sso_login.prompt_sso_login(Mock(), remote, on_done=on_done)

    on_done.assert_called_once_with(False)
    sso_spies["dialog"].assert_not_called()
    sso_spies["thread"].assert_not_called()
    message = sso_spies["showerror"].call_args.args[1]
    assert "disabled by your organization" in message


def test_sso_login_starts_over_an_allowed_channel(sso_spies, monkeypatch):
    monkeypatch.setattr("Pytre_Core.remotes.plain_http_allowed", lambda: False)
    remote = Remote(None, RemoteAddress(url="https://pytre.example.com", port=443, username="alice"))
    monkeypatch.setattr(sso_login, "tk_call_when_ready", Mock())

    sso_login.prompt_sso_login(Mock(), remote, on_done=Mock())

    sso_spies["showerror"].assert_not_called()
    sso_spies["dialog"].assert_called_once()
    sso_spies["thread"].return_value.start.assert_called_once()
