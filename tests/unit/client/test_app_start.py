"""
Tests for Pytre_Client/app.py app_start() and install_update() - App and Updater are replaced by mocks,
so these tests never start a UI nor touch a real executable
"""

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from Pytre_Client.app import app_start, install_update
from Pytre_Core.context import AppMode
from Pytre_Core.updater import UpdateError

CURRENT_EXE = Path("C:/Apps/Pytre.exe")
NEW_EXE = Path("C:/Apps/Pytre.exe.new")


@pytest.fixture(autouse=True)
def mock_updater():
    """Never let app_start() rename or restart the real sys.executable - not an official build by default"""
    with patch("Pytre_Client.app.Updater") as updater:
        updater.is_updatable.return_value = False
        updater.current_exe.return_value = CURRENT_EXE
        yield updater


@pytest.fixture(autouse=True)
def app_without_pending_update():
    """App mock whose UI loop ends without any downloaded update - a bare Mock attribute would be truthy"""
    with patch("Pytre_Client.app.App") as mock_app:
        mock_app.return_value.pending_update = None
        mock_app.return_value.restart_after_update = False
        yield mock_app


@pytest.mark.unit
@pytest.mark.parametrize("detected_mode", [AppMode.HYBRID, AppMode.CLIENT])
def test_app_start_without_mode_uses_detected_mode(detected_mode):
    with (
        patch("Pytre_Client.app.App") as MockApp,
        patch("Pytre_Client.app.detect_client_mode", return_value=detected_mode) as mock_detect,
        patch("Pytre_Client.app.secret_key_available", return_value=True),
    ):
        app_start()

    mock_detect.assert_called_once_with()
    MockApp.assert_called_once_with(detected_mode)
    MockApp.return_value.mainloop.assert_called_once()


@pytest.mark.unit
def test_app_start_with_explicit_mode_skips_detection():
    """--init-hybrid: HYBRID is forced even if the settings database doesn't exist yet"""
    with (
        patch("Pytre_Client.app.App") as MockApp,
        patch("Pytre_Client.app.detect_client_mode") as mock_detect,
        patch("Pytre_Client.app.secret_key_available", return_value=True),
    ):
        app_start(AppMode.HYBRID)

    mock_detect.assert_not_called()
    MockApp.assert_called_once_with(AppMode.HYBRID)


@pytest.mark.unit
def test_app_start_swallows_system_exit():
    """App exits through SystemExit (access refused, outdated version...) - app_start must end quietly"""
    with (
        patch("Pytre_Client.app.App", Mock(side_effect=SystemExit)),
        patch("Pytre_Client.app.detect_client_mode", return_value=AppMode.CLIENT),
    ):
        app_start()


@pytest.mark.unit
@pytest.mark.parametrize("mode", [None, AppMode.HYBRID])
def test_app_start_in_hybrid_mode_without_secret_key_shows_a_message_instead_of_starting(mode):
    """Build without credentials_secrets.py: an explanation instead of a traceback, App never starts"""
    with (
        patch("Pytre_Client.app.App") as MockApp,
        patch("Pytre_Client.app.detect_client_mode", return_value=AppMode.HYBRID),
        patch("Pytre_Client.app.secret_key_available", return_value=False),
        patch("Pytre_Client.app.show_hybrid_unavailable") as mock_show,
    ):
        app_start(mode)

    mock_show.assert_called_once_with()
    MockApp.assert_not_called()


@pytest.mark.unit
def test_app_start_in_client_mode_does_not_need_the_secret_key():
    with (
        patch("Pytre_Client.app.App") as MockApp,
        patch("Pytre_Client.app.detect_client_mode", return_value=AppMode.CLIENT),
        patch("Pytre_Client.app.secret_key_available", return_value=False),
        patch("Pytre_Client.app.show_hybrid_unavailable") as mock_show,
    ):
        app_start()

    mock_show.assert_not_called()
    MockApp.assert_called_once_with(AppMode.CLIENT)


# ================================================================================
# Self-update
# ================================================================================


@pytest.mark.unit
@pytest.mark.parametrize("updatable, cleaned", [(True, True), (False, False)])
def test_app_start_deletes_the_previous_executable_of_an_official_build(mock_updater, updatable, cleaned):
    mock_updater.is_updatable.return_value = updatable
    with patch("Pytre_Client.app.detect_client_mode", return_value=AppMode.CLIENT):
        app_start()

    if cleaned:
        mock_updater.cleanup_previous.assert_called_once_with(CURRENT_EXE)
    else:
        mock_updater.cleanup_previous.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("restart", [True, False])
def test_app_start_installs_the_pending_update_once_the_ui_loop_has_ended(
    mock_updater, app_without_pending_update, restart
):
    mock_updater.is_updatable.return_value = True
    my_app = app_without_pending_update.return_value
    my_app.pending_update = NEW_EXE
    my_app.restart_after_update = restart
    calls = MagicMock()
    my_app.mainloop.side_effect = lambda: calls.mainloop()
    mock_updater.install.side_effect = lambda current, new: calls.install(current, new)

    with patch("Pytre_Client.app.detect_client_mode", return_value=AppMode.CLIENT):
        app_start()

    assert [call[0] for call in calls.mock_calls] == ["mainloop", "install"]
    mock_updater.install.assert_called_once_with(CURRENT_EXE, NEW_EXE)
    if restart:
        mock_updater.restart.assert_called_once_with(CURRENT_EXE)
    else:
        mock_updater.restart.assert_not_called()


@pytest.mark.unit
def test_app_start_without_pending_update_installs_nothing(mock_updater):
    mock_updater.is_updatable.return_value = True
    with patch("Pytre_Client.app.detect_client_mode", return_value=AppMode.CLIENT):
        app_start()

    mock_updater.install.assert_not_called()
    mock_updater.restart.assert_not_called()


@pytest.mark.unit
def test_app_start_never_installs_outside_an_official_build(mock_updater, app_without_pending_update):
    """Run from sources or a build with the key: the running executable is the Python interpreter or an
    administrator-managed build, it must never be renamed even if a pending update is somehow set"""
    my_app = app_without_pending_update.return_value
    my_app.pending_update = NEW_EXE
    my_app.restart_after_update = True

    with patch("Pytre_Client.app.detect_client_mode", return_value=AppMode.CLIENT):
        app_start()

    mock_updater.install.assert_not_called()
    mock_updater.restart.assert_not_called()


@pytest.mark.unit
def test_app_start_exiting_during_init_installs_nothing(mock_updater, app_without_pending_update):
    mock_updater.is_updatable.return_value = True
    app_without_pending_update.side_effect = SystemExit
    with patch("Pytre_Client.app.detect_client_mode", return_value=AppMode.CLIENT):
        app_start()

    mock_updater.install.assert_not_called()


@pytest.mark.unit
def test_install_update_swaps_and_restarts(mock_updater):
    install_update(NEW_EXE, restart=True)

    mock_updater.install.assert_called_once_with(CURRENT_EXE, NEW_EXE)
    mock_updater.restart.assert_called_once_with(CURRENT_EXE)


@pytest.mark.unit
def test_install_update_failure_shows_an_error_and_does_not_restart(mock_updater):
    mock_updater.install.side_effect = UpdateError("Pytre.exe is locked")

    with (
        patch("Pytre_Client.app.tk.Tk") as mock_tk,
        patch("Pytre_Client.app.messagebox.showerror") as mock_showerror,
    ):
        install_update(NEW_EXE, restart=True)

    mock_updater.restart.assert_not_called()
    mock_showerror.assert_called_once()
    assert "Pytre.exe is locked" in mock_showerror.call_args.args[1]
    mock_tk.return_value.destroy.assert_called_once()
