"""
Tests for the enterprise update policy in Pytre_Client/app.py and Pytre_Client/app_about.py. The methods are
called with a fake `self`: they only need a few attributes, and building a real window requires a display.
"""

from unittest.mock import Mock, patch

import pytest

from Pytre_Client.app import App
from Pytre_Client.app_about import AboutWindow
from Pytre_Core.about import AppStage
from Pytre_Core.updater import UpdatePolicy
from Pytre_Core.user_prefs import PrefEnum

pytestmark = pytest.mark.unit


def fake_app(policy: UpdatePolicy, updatable: bool = True, startup_check: bool = True) -> Mock:
    app = Mock()
    app.updatable = updatable
    app.update_policy = policy
    app.user_ctx.user_prefs.get.side_effect = lambda pref: {
        PrefEnum.UPDATE_CHECK: startup_check,
        PrefEnum.UPDATE_CHANNEL: AppStage.RELEASE,
        PrefEnum.UPDATE_SKIPPED: "",
    }[pref]
    return app


def fake_about(policy: UpdatePolicy, check: bool = True, channel: str = "RELEASE") -> Mock:
    """AboutWindow whose user kept the startup check on and the RELEASE channel, then selected `check`/`channel`"""
    about = Mock()
    about.update_policy = policy
    about.update_check = True
    about.update_channel = AppStage.RELEASE
    about.upd_checkbox_var.get.return_value = check
    about.upd_combo.get.return_value = channel
    return about


# ================================================================================
# Startup check
# ================================================================================


@pytest.mark.parametrize(
    "policy, updatable, startup_check, expected",
    [
        (UpdatePolicy.ALLOWED, True, True, True),
        (UpdatePolicy.ALLOWED, True, False, False),
        (UpdatePolicy.ALLOWED, False, True, False),
        (UpdatePolicy.DISABLED, True, True, False),
    ],
)
def test_startup_check_needs_an_updatable_build_the_user_pref_and_an_allowing_policy(
    policy, updatable, startup_check, expected
):
    app = fake_app(policy, updatable=updatable, startup_check=startup_check)

    App.check_for_update_at_startup(app)

    assert app.check_for_update.called is expected


# ================================================================================
# Check for update
# ================================================================================


@pytest.mark.parametrize("manual", [True, False])
def test_check_for_update_never_looks_for_a_release_when_disabled(manual):
    """No Updater means no request to GitHub, whichever way the check was triggered"""
    app = fake_app(UpdatePolicy.DISABLED)

    with (
        patch("Pytre_Client.app.Updater") as updater,
        patch("Pytre_Client.app.Thread") as thread,
        patch("Pytre_Client.app.MsgOverlay") as overlay,
    ):
        App.check_for_update(app, manual=manual)

    updater.assert_not_called()
    thread.assert_not_called()
    overlay.display.assert_not_called()


def test_check_for_update_looks_for_a_release_when_allowed():
    app = fake_app(UpdatePolicy.ALLOWED)

    with (
        patch("Pytre_Client.app.Updater") as updater,
        patch("Pytre_Client.app.Thread") as thread,
        patch("Pytre_Client.app.MsgOverlay"),
        patch("Pytre_Client.app.tk_call_when_ready"),
    ):
        App.check_for_update(app, manual=True)

    updater.assert_called_once()
    thread.return_value.start.assert_called_once()


# ================================================================================
# About window preferences
# ================================================================================


def test_about_saves_nothing_when_updates_are_disabled():
    about = fake_about(UpdatePolicy.DISABLED, check=False, channel="BETA")

    AboutWindow.save_channel(about)

    about.user_prefs.set.assert_not_called()
    about.user_prefs.save.assert_not_called()


def test_about_saves_the_startup_check_and_channel_when_allowed():
    about = fake_about(UpdatePolicy.ALLOWED, check=False, channel="BETA")

    AboutWindow.save_channel(about)

    about.user_prefs.set.assert_any_call(PrefEnum.UPDATE_CHECK, False, save=False)
    about.user_prefs.set.assert_any_call(PrefEnum.UPDATE_CHANNEL, AppStage.BETA, save=False)
    about.user_prefs.save.assert_called_once()


def test_about_saves_nothing_when_closed_without_ok():
    about = fake_about(UpdatePolicy.ALLOWED, check=False)
    about.updatable = True
    about.parent = None

    AboutWindow.app_exit(about, save_prefs=False)

    about.save_channel.assert_not_called()
