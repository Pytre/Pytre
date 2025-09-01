import tkinter as tk
from tkinter import Event, ttk

if not __package__:
    import syspath_insert  # noqa: F401  # disable unused-import warning

import utils
from kee import Kee
from about import APP_NAME
from ui.app_theme import set_theme, ThemeColors, theme_is_on


class PasswordWindow(tk.Toplevel):
    def __init__(self, parent: tk.Tk | None = None):
        super().__init__()

        self.parent: tk.Tk = parent
        if not self.parent:
            self.master.withdraw()
        else:
            self.focus_set()
            utils.ui_disable_parent(self, self.parent)
            self.transient(self.parent)

        self.kee: Kee = Kee()
        self.kee._open_db(True)
        self.history: list[str] = self.kee.pwd_history()

        set_theme(self)
        self._setup_ui()
        self._events_binds()

        self.set_history()

    def _setup_ui(self):
        self.title(f"{APP_NAME} - Accès aux paramètres, mot de passe")
        self.minsize(width=400, height=25)
        if self.parent:
            self.geometry(f"+{self.parent.winfo_x() + 200}+{self.parent.winfo_y() + 150}")
        else:
            utils.ui_center(self)
        self.resizable(True, False)

        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

        self.input_frame = ttk.Frame(self, padding=1, borderwidth=2)
        self.history_frame = ttk.Frame(self, padding=1, borderwidth=2)
        self.buttons_frame = ttk.Frame(self, padding=1, borderwidth=2)

        self.input_frame.grid(row=0, column=0, padx=4, pady=2, sticky="nswe")
        self.history_frame.grid(row=1, column=0, padx=4, pady=0, sticky="nswe")
        self.buttons_frame.grid(row=2, column=0, padx=4, pady=2, sticky="nswe")

        self._setup_input()
        self._setup_history()
        self._setup_buttons()

    def _setup_input(self):
        self.input_frame.columnconfigure(1, weight=1)
        self.input_frame.rowconfigure(1, weight=1)

        label = ttk.Label(self.input_frame, text="Mot de passe :")
        self.tk_var_pwd = tk.StringVar(value=self.history[0])
        self.entry = ttk.Entry(self.input_frame, textvariable=self.tk_var_pwd)
        self.entry.icursor(tk.END)

        self.reveal = ttk.Button(self.input_frame, width=8)
        self.reveal.config(command=self.toggle_password)
        self.toggle_password()

        label.grid(row=0, column=0, padx=2, pady=2, sticky="nswe")
        self.entry.grid(row=0, column=1, padx=2, pady=2, sticky="nswe")
        self.reveal.grid(row=0, column=3, padx=2, pady=2, sticky="nswe")

        self.entry.focus_set()

    def _setup_history(self):
        self.history_frame.columnconfigure(0, weight=1)
        self.history_frame.rowconfigure(0, weight=1)

        self.listbox = tk.Listbox(self.history_frame, height=10, activestyle="none", relief="groove")
        if theme_is_on():
            self.listbox.configure(selectbackground=ThemeColors.accent, selectforeground=ThemeColors.text_secondary)
        ybar = ttk.Scrollbar(self.history_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscroll=ybar.set)

        self.listbox.grid(row=0, column=0, padx=2, pady=2, sticky="nswe")
        ybar.grid(row=0, column=1, padx=2, pady=2, sticky="nse")

        self.history_frame.grid_remove()

    def _setup_buttons(self):
        self.buttons_frame.columnconfigure(0, weight=1)

        btn_history = ttk.Button(self.buttons_frame, text="Historique", command=self.toggle_history)
        self.btn_modify = ttk.Button(self.buttons_frame, text="Modifier", command=self.set_change_pwd)
        self.btn_cancel = ttk.Button(self.buttons_frame, text="Annuler", command=self.close)

        btn_history.grid(row=0, column=0, padx=2, pady=2, sticky="nsw")
        self.btn_modify.grid(row=0, column=1, padx=2, pady=2, sticky="nse")
        self.btn_cancel.grid(row=0, column=2, padx=2, pady=2, sticky="nse")

    def _events_binds(self):
        self.entry.bind("<Return>", lambda _: self.set_change_pwd())
        self.listbox.bind("<Double-Button-1>", self.copy_to_entry)
        self.protocol("WM_DELETE_WINDOW", self.close)  # arrêter le programme quand fermeture de la fenêtre

    def toggle_password(self, hide_char: str = "\U000025cf"):
        if self.entry["show"] == "":
            self.reveal.config(text="Voir")
            self.entry.config(show=hide_char)
        else:
            self.reveal.config(text="Masquer")
            self.entry.config(show="")

        self.entry.focus_set()

    def toggle_history(self):
        self.history_shown: bool
        if not hasattr(self, "history_shown") or not self.history_shown:
            self.history_frame.grid()
            self.history_shown = True
            if not self.entry["show"] == "":
                self.toggle_password()
        else:
            self.history_frame.grid_remove()
            self.history_shown = False

    def set_history(self):
        for pwd in self.history:
            self.listbox.insert(tk.END, pwd)

    def copy_to_entry(self, _: Event):
        pwd = self.listbox.get(tk.ACTIVE)
        self.tk_var_pwd.set(pwd)
        self.entry.icursor(tk.END)

    def set_change_pwd(self):
        new_pwd = self.tk_var_pwd.get()
        self.kee.pwd_change(new_pwd)
        self.close()

    def close(self, _: Event = None):
        if self.parent:
            utils.ui_undisable_parent(self, self.parent)
            self.parent.focus_set()
            self.destroy()  # doit se faire après avoir rendu le focus
        else:
            self.destroy()
            self.quit()


if __name__ == "__main__":
    my_app = PasswordWindow()
    my_app.mainloop()
