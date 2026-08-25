"""
Tests for Pytre_Client/ui_utils_csv.py

csv_process_async() drives MsgOverlay (real Tk widgets) and tk_call_when_ready (real Tk
.after() polling) - both are already covered by their own tests, so here they're replaced
with lightweight fakes to test csv_process_async()'s own wiring/logic in isolation, without
needing a real Tk root or a real polling loop.
"""

from unittest.mock import Mock

import pytest

from Pytre_Client.ui_utils_csv import CsvTaskConfig, csv_process_async


def _fake_tk_call_when_ready(window, queue, callback):
    """Replaces the real Tk .after()-based polling with a blocking wait - worker() runs in a
    real background thread and puts exactly one result on the queue when done."""
    result = queue.get(timeout=2)
    callback(*result) if isinstance(result, tuple) else callback(result)


@pytest.fixture
def overlay():
    return Mock()


@pytest.fixture
def showinfo(monkeypatch):
    mock = Mock()
    monkeypatch.setattr("Pytre_Client.ui_utils_csv.messagebox.showinfo", mock)
    return mock


@pytest.fixture
def showerror(monkeypatch):
    mock = Mock()
    monkeypatch.setattr("Pytre_Client.ui_utils_csv.messagebox.showerror", mock)
    return mock


@pytest.fixture(autouse=True)
def _patch_collaborators(monkeypatch, overlay):
    monkeypatch.setattr("Pytre_Client.ui_utils_csv.tk_call_when_ready", _fake_tk_call_when_ready)
    monkeypatch.setattr("Pytre_Client.ui_utils_csv.MsgOverlay.display", Mock(return_value=overlay))


@pytest.mark.unit
def test_locks_ui_and_shows_overlay_before_starting_work(monkeypatch, showinfo):
    display = Mock(return_value=Mock())
    monkeypatch.setattr("Pytre_Client.ui_utils_csv.MsgOverlay.display", display)
    ui_lock_fn = Mock()
    ui_parent = Mock()

    cfg = CsvTaskConfig(
        ui_parent=ui_parent,
        ui_lock_fn=ui_lock_fn,
        ui_unlock_fn=Mock(),
        overlay_dialog_msg="Chargement...",
        overlay_dialog_wait_ms=500,
        csv_process_fn=Mock(return_value=True),
        end_msgbox_title="Titre",
        end_msgbox_msg="Message ok",
    )

    csv_process_async(cfg)

    ui_lock_fn.assert_called_once()
    display.assert_called_once_with(ui_parent, "Chargement...", 500)


@pytest.mark.unit
def test_success_shows_info_message_and_calls_end_fn(showinfo, showerror):
    end_fn = Mock()
    ui_parent = Mock()

    cfg = CsvTaskConfig(
        ui_parent=ui_parent,
        ui_lock_fn=Mock(),
        ui_unlock_fn=Mock(),
        overlay_dialog_msg="Chargement...",
        overlay_dialog_wait_ms=500,
        csv_process_fn=Mock(return_value=True),
        end_msgbox_title="Titre",
        end_msgbox_msg="Message ok",
        end_fn=end_fn,
    )

    csv_process_async(cfg)

    showinfo.assert_called_once_with(title="Titre", message="Message ok", parent=ui_parent)
    assert not showerror.called
    end_fn.assert_called_once_with(True)


@pytest.mark.unit
def test_overlay_hide_receives_the_unlock_callback(overlay, showinfo):
    ui_unlock_fn = Mock()

    cfg = CsvTaskConfig(
        ui_parent=Mock(),
        ui_lock_fn=Mock(),
        ui_unlock_fn=ui_unlock_fn,
        overlay_dialog_msg="Chargement...",
        overlay_dialog_wait_ms=500,
        csv_process_fn=Mock(return_value=True),
        end_msgbox_title="Titre",
        end_msgbox_msg="Message ok",
    )

    csv_process_async(cfg)

    overlay.hide.assert_called_once_with(callback=ui_unlock_fn)


@pytest.mark.unit
def test_exception_from_csv_process_fn_shows_error_with_exception_text(showinfo, showerror):
    end_fn = Mock()
    boom = ValueError("disk full")

    cfg = CsvTaskConfig(
        ui_parent=Mock(),
        ui_lock_fn=Mock(),
        ui_unlock_fn=Mock(),
        overlay_dialog_msg="Chargement...",
        overlay_dialog_wait_ms=500,
        csv_process_fn=Mock(side_effect=boom),
        end_msgbox_title="Titre",
        end_msgbox_msg="Message ok",
        end_fn=end_fn,
    )

    csv_process_async(cfg)

    assert not showinfo.called
    _, kwargs = showerror.call_args
    assert kwargs["title"] == "Titre"
    assert "disk full" in kwargs["message"]
    end_fn.assert_called_once_with(False)


@pytest.mark.unit
def test_result_false_without_exception_still_shows_error(showinfo, showerror):
    """Documents current behaviour: csv_process_fn returning False (no exception raised)
    takes the same error branch as a real exception - the message then just shows "None"
    for the missing error, since `error` stays None in that case."""
    cfg = CsvTaskConfig(
        ui_parent=Mock(),
        ui_lock_fn=Mock(),
        ui_unlock_fn=Mock(),
        overlay_dialog_msg="Chargement...",
        overlay_dialog_wait_ms=500,
        csv_process_fn=Mock(return_value=False),
        end_msgbox_title="Titre",
        end_msgbox_msg="Message ok",
    )

    csv_process_async(cfg)

    _, kwargs = showerror.call_args
    assert "None" in kwargs["message"]


@pytest.mark.unit
def test_end_fn_is_optional(showinfo, showerror):
    cfg = CsvTaskConfig(
        ui_parent=Mock(),
        ui_lock_fn=Mock(),
        ui_unlock_fn=Mock(),
        overlay_dialog_msg="Chargement...",
        overlay_dialog_wait_ms=500,
        csv_process_fn=Mock(return_value=True),
        end_msgbox_title="Titre",
        end_msgbox_msg="Message ok",
    )

    csv_process_async(cfg)  # must not raise
