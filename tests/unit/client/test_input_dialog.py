"""
Tests for Pytre_Client/InputDialog.py
"""

import gc
import tkinter as tk

import pytest

from Pytre_Client.InputDialog import InputDialog


@pytest.fixture
def root():
    r = tk.Tk()
    r.withdraw()
    yield r
    r.destroy()
    # InputDialog creates a tk.StringVar, which can form a reference cycle with the widget
    # tree - without an explicit collect, it can outlive this root and be __del__'d much
    # later (once Tcl's interpreter is long gone), surfacing as an unrelated warning in a
    # totally different test module.
    gc.collect()


@pytest.fixture
def dialog(root):
    d = InputDialog("Titre", "Entrez une valeur :", parent=root)
    yield d
    if d.winfo_exists():
        d.destroy()


@pytest.mark.unit
def test_set_answer_and_close_stores_the_entry_text_and_closes(dialog):
    dialog.tk_var_answer.set("some value")

    dialog.set_answer_and_close()

    assert dialog.answer == "some value"
    assert not dialog.winfo_exists()


@pytest.mark.unit
def test_close_without_answering_leaves_answer_none(dialog):
    dialog.close()

    assert dialog.answer is None
    assert not dialog.winfo_exists()


@pytest.mark.unit
def test_password_mode_masks_the_entry(root):
    dialog = InputDialog("Titre", "Mot de passe :", parent=root, is_pwd=True)

    assert dialog.entry["show"] == "*"

    dialog.destroy()


@pytest.mark.unit
def test_normal_mode_does_not_mask_the_entry(root):
    dialog = InputDialog("Titre", "Valeur :", parent=root, is_pwd=False)

    assert dialog.entry["show"] == ""

    dialog.destroy()
