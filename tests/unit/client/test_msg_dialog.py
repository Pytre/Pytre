"""
Tests for Pytre_Client/MsgDialog.py

select_btn_move()/on_click() have real, independent logic (position wraparound, resolving
which button "Enter" should activate from keyboard focus) built entirely around
self.focus_displayof(). Real OS/window-manager focus tracking doesn't work reliably on a
withdrawn (never-shown) Toplevel (confirmed empirically), so focus_displayof() is stubbed
per test instead of relying on it - this decouples the tests from window-manager focus
policy while still exercising MsgDialog's own logic for real.
"""

import tkinter as tk

import pytest

from Pytre_Client.MsgDialog import MsgDialog


@pytest.fixture
def root():
    r = tk.Tk()
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture
def dialog(root):
    d = MsgDialog("Titre", "Message", ("Ouvrir", "Enregistrer", "Annuler"), parent=root)
    yield d
    if d.winfo_exists():
        d.destroy()


def _focused_on(dialog, widget):
    dialog.focus_displayof = lambda: widget


# ------------------------------------------------------------------------------
# select_btn_move
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_moves_focus_forward_from_the_focused_button(dialog):
    _focused_on(dialog, dialog.buttons_widgets[0])
    calls = []
    dialog.buttons_widgets[1].focus_set = lambda: calls.append(1)

    dialog.select_btn_move(1)

    assert calls == [1]


@pytest.mark.unit
def test_moves_focus_backward_from_the_focused_button(dialog):
    _focused_on(dialog, dialog.buttons_widgets[2])
    calls = []
    dialog.buttons_widgets[1].focus_set = lambda: calls.append(1)

    dialog.select_btn_move(-1)

    assert calls == [1]


@pytest.mark.unit
def test_moving_forward_past_the_last_button_wraps_to_the_first(dialog):
    _focused_on(dialog, dialog.buttons_widgets[2])
    calls = []
    dialog.buttons_widgets[0].focus_set = lambda: calls.append(0)

    dialog.select_btn_move(1)

    assert calls == [0]


@pytest.mark.unit
def test_moving_backward_past_the_first_button_wraps_to_the_last(dialog):
    _focused_on(dialog, dialog.buttons_widgets[0])
    calls = []
    dialog.buttons_widgets[2].focus_set = lambda: calls.append(2)

    dialog.select_btn_move(-1)

    assert calls == [2]


@pytest.mark.unit
def test_moving_forward_with_nothing_focused_selects_the_first_button(dialog):
    _focused_on(dialog, None)
    calls = []
    dialog.buttons_widgets[0].focus_set = lambda: calls.append(0)

    dialog.select_btn_move(1)

    assert calls == [0]


@pytest.mark.unit
def test_moving_backward_with_nothing_focused_selects_the_last_button(dialog):
    _focused_on(dialog, None)
    calls = []
    dialog.buttons_widgets[2].focus_set = lambda: calls.append(2)

    dialog.select_btn_move(-1)

    assert calls == [2]


# ------------------------------------------------------------------------------
# on_click
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_on_click_with_explicit_text_sets_answer(dialog):
    dialog.on_click("Ouvrir")

    assert dialog.button_clicked == "Ouvrir"
    assert not dialog.winfo_exists()  # close() destroys the dialog


@pytest.mark.unit
def test_on_click_none_uses_the_text_of_the_focused_button(dialog):
    _focused_on(dialog, dialog.buttons_widgets[1])  # "Enregistrer"

    dialog.on_click(None)

    assert dialog.button_clicked == "Enregistrer"


@pytest.mark.unit
def test_on_click_none_does_nothing_when_focus_is_not_on_a_button(dialog):
    _focused_on(dialog, None)

    dialog.on_click(None)

    assert dialog.button_clicked is None
    assert dialog.winfo_exists()  # dialog stays open
