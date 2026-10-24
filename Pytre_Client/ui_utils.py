import platform
import tkinter as tk
from collections.abc import Callable, Sequence
from tkinter import ttk, font


def ui_center(tk_window: tk.Tk | tk.Toplevel, tk_parent: tk.Tk | tk.Toplevel | None = None) -> None:
    if platform.system() != "Windows" and not tk_parent:
        return

    tk_window.update_idletasks()

    parent_x = tk_parent.winfo_x() if tk_parent else 0
    parent_y = tk_parent.winfo_y() if tk_parent else 0
    parent_width = tk_parent.winfo_width() if tk_parent else tk_window.winfo_screenwidth()
    parent_height = tk_parent.winfo_height() if tk_parent else tk_window.winfo_screenheight()

    x = int(parent_x + parent_width / 2 - tk_window.winfo_width() / 2)
    y = int(parent_y + parent_height / 2 - tk_window.winfo_height() / 1.8)

    tk_window.geometry(f"+{x}+{y}")


def ui_disable_parent(tk_window: tk.Tk | tk.Toplevel, tk_parent: tk.Tk | tk.Toplevel | None = None) -> None:
    system: str = platform.system()

    if system != "Windows":
        tk_window.grab_set()
    elif tk_parent:
        tk_parent.wm_attributes("-disabled", True)


def ui_undisable_parent(tk_window: tk.Tk | tk.Toplevel, tk_parent: tk.Tk | tk.Toplevel | None = None) -> None:
    system: str = platform.system()

    if system != "Windows":
        tk_window.grab_release()
    elif tk_parent:
        tk_parent.wm_attributes("-disabled", False)


def textbox_ctx_menu(textbox: tk.Text, extra_items: list[tuple[str, Callable[[], None]]] | None = None) -> tk.Menu:
    """Adds a right-click menu to `textbox`: Copy, Select all, then `extra_items` as (label, command).
    Returns the menu, so a caller can still add entries to it."""

    def show_menu(event: tk.Event):
        has_selection = bool(textbox.tag_ranges(tk.SEL))
        menu.entryconfigure(0, state="normal" if has_selection else "disabled")
        menu.tk_popup(event.x_root, event.y_root)

    menu = tk.Menu(textbox, tearoff=0)
    menu.add_command(label="Copier (Ctrl+C)", command=lambda: textbox.event_generate("<<Copy>>"))
    menu.add_command(label="Tout sélectionner (Ctrl+A)", command=lambda: textbox.event_generate("<<SelectAll>>"))

    if extra_items:
        menu.add_separator()
        for label, command in extra_items:
            menu.add_command(label=label, command=command)

    textbox.bind("<Button-3>", show_menu)
    textbox.bind("<Control-a>", lambda _: (textbox.tag_add(tk.SEL, "1.0", tk.END), "break")[1])

    return menu


def listbox_add_sorted(listbox: tk.Listbox, new_value: str | None) -> bool:
    """Insère new_value dans listbox en position triée.
    Ignore les doublons (insensibles à la casse).
    Retourne True si inséré, False si ignoré (vide ou doublon)."""

    curr_values: list[str] = listbox.get(0, tk.END)
    if not new_value or any(new_value.lower() == v.lower() for v in curr_values):
        return False

    pos = 0
    for line in curr_values:
        if new_value > line:
            pos += 1
        else:
            break

    listbox.insert(pos, new_value)
    listbox.selection_set(pos)
    return True


def toggle_password(w_caller: ttk.Button, w_target: ttk.Entry, hide_char: str = "\U000025cf") -> None:
    if w_target["show"] == "":
        w_caller.config(text="Voir")
        w_target.config(show=hide_char)
    else:
        w_caller.config(text="Masquer")
        w_target.config(show="")


def tree_select_pos(tree: ttk.Treeview, pos: int) -> None:
    max_pos = len(tree.get_children()) - 1

    if max_pos < 0:
        return
    elif pos == -1:
        pos = max_pos
    elif pos < 0:
        pos = 0
    elif pos > max_pos:
        pos = max_pos

    item = tree.get_children()[pos]
    tree.selection_set(item)
    tree.focus(item)
    tree.see(item)


def tree_select_move(tree: ttk.Treeview, offset: int) -> None:
    curr_item = tree.selection()
    if not curr_item == ():
        pos = tree.index(curr_item[0])
    else:
        pos = 0

    tree_select_pos(tree, pos + offset)


def tree_autosize(
    tree: ttk.Treeview,
    columns: Sequence[int | str] | None = None,
    min_size: int = 0,
    extra_width: int = 0,
) -> None:
    cols_to_autosize = columns if columns is not None else tree["columns"]

    style = ttk.Style(tree)
    style_name = tree.cget("style") or "Treeview"
    font_spec = style.lookup(style_name, "font") or "TkDefaultFont"
    tkfont = font.Font(font=font_spec)

    for col in cols_to_autosize:
        max_width = tkfont.measure(tree.heading(col)["text"] + "    ") + extra_width

        for item in tree.get_children(""):
            item_width = tkfont.measure(str(tree.set(item, col)) + "    ")
            max_width = max(max_width, item_width)

        max_width = max(max_width, min_size)
        tree.column(col, width=max_width)
