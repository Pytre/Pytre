import platform
import tkinter as tk


def ui_center(tk_window: tk.Toplevel, tk_parent: tk.Toplevel | None = None) -> None:
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


def ui_disable_parent(tk_window: tk.Toplevel, tk_parent: tk.Toplevel | None = None) -> None:
    system: str = platform.system()

    if system == "Windows":
        tk_parent.wm_attributes("-disabled", True)
    else:
        tk_window.grab_set()


def ui_undisable_parent(tk_window: tk.Toplevel, tk_parent: tk.Toplevel | None = None) -> None:
    system: str = platform.system()

    if system == "Windows":
        tk_parent.wm_attributes("-disabled", False)
    else:
        tk_window.grab_release()
