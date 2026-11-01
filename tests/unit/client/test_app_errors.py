"""
Tests for Pytre_Client/app.py's error reporting. Called with a fake `self`: the methods under test
only need these few attributes, and building a real App requires a display.
"""

import logging
from unittest.mock import Mock, patch

import pytest

from Pytre_Client.app import App


def _raised(error: Exception) -> Exception:
    try:
        raise error
    except Exception as e:
        return e


@pytest.mark.unit
def test_on_callback_error_logs_the_traceback_and_shows_the_message(caplog):
    error = _raised(OSError("Failed to save"))

    with patch("Pytre_Client.app.messagebox.showerror") as showerror, caplog.at_level(logging.ERROR):
        App.on_callback_error(Mock(), OSError, error, error.__traceback__)

    showerror.assert_called_once()
    assert showerror.call_args.kwargs["message"] == "Failed to save"
    assert any("Traceback" in r.message and "Failed to save" in r.message for r in caplog.records)


@pytest.mark.unit
def test_ensure_admin_exists_removes_the_admin_right_if_it_cant_be_saved():
    app = Mock()
    app.app_ctx.acts_as_server = True
    app.app_ctx.users.users_admin_exists.return_value = False
    app.user_ctx.user.admin = False
    app.user_ctx.user.save.side_effect = OSError("Failed to save")

    with patch("Pytre_Client.app.messagebox") as messagebox:
        App.ensure_admin_exists(app)

    assert app.user_ctx.user.admin is False
    messagebox.showerror.assert_called_once()
    assert "Failed to save" in messagebox.showerror.call_args.kwargs["message"]


@pytest.mark.unit
def test_ensure_admin_exists_keeps_the_admin_right_once_saved():
    app = Mock()
    app.app_ctx.acts_as_server = True
    app.app_ctx.users.users_admin_exists.return_value = False
    app.user_ctx.user.admin = False

    with patch("Pytre_Client.app.messagebox") as messagebox:
        App.ensure_admin_exists(app)

    assert app.user_ctx.user.admin is True
    messagebox.showerror.assert_not_called()
