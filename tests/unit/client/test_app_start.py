"""
Tests for Pytre_Client/app.py app_start() - mode resolution only, App itself is replaced by a mock
"""

from unittest.mock import Mock, patch

import pytest

from Pytre_Client.app import app_start
from Pytre_Core.context import AppMode


@pytest.mark.unit
@pytest.mark.parametrize("detected_mode", [AppMode.HYBRID, AppMode.CLIENT])
def test_app_start_without_mode_uses_detected_mode(detected_mode):
    with (
        patch("Pytre_Client.app.App") as MockApp,
        patch("Pytre_Client.app.detect_client_mode", return_value=detected_mode) as mock_detect,
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
