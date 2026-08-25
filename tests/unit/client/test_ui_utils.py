"""
Tests for Pytre_Client/ui_utils.py

listbox_add_sorted/tree_select_pos/tree_select_move/toggle_password have real, independent
logic worth locking in - unlike most of this module (ui_center, ui_disable_parent, ...),
which are thin wrappers around live widget geometry with little logic of their own. These
four are tested here against real (hidden) Tk widgets, since a display is available.
"""

import tkinter as tk
from tkinter import ttk

import pytest

from Pytre_Client.ui_utils import listbox_add_sorted, toggle_password, tree_select_move, tree_select_pos


@pytest.fixture
def root():
    r = tk.Tk()
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture
def listbox(root):
    return tk.Listbox(root)


@pytest.fixture
def tree(root):
    t = ttk.Treeview(root)
    for label in ("first", "second", "third"):
        t.insert("", "end", iid=label, text=label)
    return t


# ------------------------------------------------------------------------------
# listbox_add_sorted
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_inserts_into_empty_listbox(listbox):
    result = listbox_add_sorted(listbox, "banana")

    assert result is True
    assert listbox.get(0, tk.END) == ("banana",)


@pytest.mark.unit
def test_inserts_in_sorted_position(listbox):
    listbox.insert(tk.END, "apple", "cherry")

    result = listbox_add_sorted(listbox, "banana")

    assert result is True
    assert listbox.get(0, tk.END) == ("apple", "banana", "cherry")


@pytest.mark.unit
def test_inserts_at_start_when_less_than_all_existing(listbox):
    listbox.insert(tk.END, "banana")

    listbox_add_sorted(listbox, "apple")

    assert listbox.get(0, tk.END) == ("apple", "banana")


@pytest.mark.unit
def test_inserts_at_end_when_greater_than_all_existing(listbox):
    listbox.insert(tk.END, "apple")

    listbox_add_sorted(listbox, "zebra")

    assert listbox.get(0, tk.END) == ("apple", "zebra")


@pytest.mark.unit
@pytest.mark.parametrize("value", ["", None])
def test_ignores_empty_or_none_value(listbox, value):
    result = listbox_add_sorted(listbox, value)

    assert result is False
    assert listbox.get(0, tk.END) == ()


@pytest.mark.unit
def test_ignores_case_insensitive_duplicate(listbox):
    listbox.insert(tk.END, "Apple")

    result = listbox_add_sorted(listbox, "apple")

    assert result is False
    assert listbox.get(0, tk.END) == ("Apple",)


@pytest.mark.unit
def test_selects_the_newly_inserted_item(listbox):
    listbox.insert(tk.END, "apple", "cherry")

    listbox_add_sorted(listbox, "banana")

    assert listbox.curselection() == (1,)


# ------------------------------------------------------------------------------
# tree_select_pos
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_selects_item_at_given_position(tree):
    tree_select_pos(tree, 1)

    assert tree.selection() == ("second",)
    assert tree.focus() == "second"


@pytest.mark.unit
def test_negative_one_selects_last_item(tree):
    tree_select_pos(tree, -1)

    assert tree.selection() == ("third",)


@pytest.mark.unit
def test_negative_position_clamps_to_first(tree):
    tree_select_pos(tree, -5)

    assert tree.selection() == ("first",)


@pytest.mark.unit
def test_position_beyond_end_clamps_to_last(tree):
    tree_select_pos(tree, 100)

    assert tree.selection() == ("third",)


@pytest.mark.unit
def test_noop_when_tree_is_empty(root):
    empty_tree = ttk.Treeview(root)

    tree_select_pos(empty_tree, 0)  # must not raise

    assert empty_tree.selection() == ()


# ------------------------------------------------------------------------------
# tree_select_move
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_moves_selection_forward_from_current(tree):
    tree_select_pos(tree, 0)

    tree_select_move(tree, 1)

    assert tree.selection() == ("second",)


@pytest.mark.unit
def test_moves_selection_backward_from_current(tree):
    tree_select_pos(tree, 2)

    tree_select_move(tree, -1)

    assert tree.selection() == ("second",)


@pytest.mark.unit
def test_defaults_to_first_item_when_nothing_selected(tree):
    tree_select_move(tree, 0)

    assert tree.selection() == ("first",)


@pytest.mark.unit
def test_move_beyond_bounds_clamps_to_last(tree):
    tree_select_pos(tree, 2)

    tree_select_move(tree, 5)

    assert tree.selection() == ("third",)


# ------------------------------------------------------------------------------
# toggle_password
# ------------------------------------------------------------------------------


@pytest.mark.unit
def test_toggles_from_visible_to_hidden(root):
    button = ttk.Button(root)
    entry = ttk.Entry(root, show="")

    toggle_password(button, entry)

    assert entry["show"] == "\U000025cf"
    assert button["text"] == "Voir"


@pytest.mark.unit
def test_toggles_from_hidden_to_visible(root):
    button = ttk.Button(root)
    entry = ttk.Entry(root, show="\U000025cf")

    toggle_password(button, entry)

    assert entry["show"] == ""
    assert button["text"] == "Masquer"


@pytest.mark.unit
def test_uses_custom_hide_char(root):
    button = ttk.Button(root)
    entry = ttk.Entry(root, show="")

    toggle_password(button, entry, hide_char="*")

    assert entry["show"] == "*"
