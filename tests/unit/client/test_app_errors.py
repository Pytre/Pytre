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


@pytest.mark.unit
def test_open_folder_shows_an_error_when_the_folder_cant_be_opened(tmp_path):
    """e.g. a queries folder on a network share that is offline"""
    folder = tmp_path / "offline_share"

    with (
        patch("Pytre_Client.app.get_cache_folder", return_value=tmp_path / "cache"),
        patch("Pytre_Client.app.utils.showfile", side_effect=FileNotFoundError(f"{folder} does not exist")),
        patch("Pytre_Client.app.messagebox.showerror") as showerror,
    ):
        App.open_folder(Mock(), folder)  # must not raise

    showerror.assert_called_once()
    assert str(folder) in showerror.call_args.args[1]


@pytest.mark.unit
def test_open_cache_folder_creates_it_when_missing(tmp_path):
    """The cache folder is created on first extraction: it may not exist yet"""
    cache = tmp_path / "cache"
    app = Mock(output_file=None)

    with (
        patch("Pytre_Client.app.get_cache_folder", return_value=cache),
        patch("Pytre_Client.app.utils.showfile") as showfile,
    ):
        App.open_folder(app, cache)

    assert cache.is_dir()
    showfile.assert_called_once_with(cache)


@pytest.mark.unit
@pytest.mark.parametrize("extract_exists", [True, False])
def test_open_cache_folder_selects_the_last_extract_only_if_it_still_exists(tmp_path, extract_exists):
    """The last extract may have been deleted by the cache cleanup or by the user"""
    cache = tmp_path / "cache"
    cache.mkdir()
    extract = cache / "query_20261107.csv"
    if extract_exists:
        extract.write_text("a;b", encoding="utf-8")
    app = Mock(output_file=extract)

    with (
        patch("Pytre_Client.app.get_cache_folder", return_value=cache),
        patch("Pytre_Client.app.utils.showfile") as showfile,
    ):
        App.open_folder(app, cache)

    showfile.assert_called_once_with(extract if extract_exists else cache)
