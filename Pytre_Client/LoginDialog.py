import tkinter as tk
from tkinter import Event, ttk, font

import Pytre_Client.ui_utils as ui_utils
from Pytre_Client.app_theme import set_theme


class LoginDialog(tk.Toplevel):
    @classmethod
    def ask(
        cls,
        title,
        prompt: str,
        login: str = "",
        password: str = "",
        parent: tk.Tk | tk.Toplevel | None = None,
    ) -> tuple[str, str] | None:

        dialog = LoginDialog(title, prompt, login=login, password=password, parent=parent)
        dialog.wait_window()
        return dialog.answer

    def __init__(
        self, title, prompt: str, parent: tk.Tk | tk.Toplevel | None = None, login: str = "", password: str = ""
    ):
        super().__init__()

        self.parent: tk.Tk | tk.Toplevel | None = parent
        if not self.parent:
            assert isinstance(self.master, tk.Tk)
            self.master.withdraw()
        else:
            self.focus_set()
            ui_utils.ui_disable_parent(self, self.parent)
            self.transient(self.parent)

        self.login = login
        self.password = password
        self.answer: tuple[str, str] | None = None

        set_theme(self)
        self._setup_ui(title, prompt)
        self._setup_position(self.parent)
        self._events_binds()

        if not self.parent:
            self.update_idletasks()  # get window on top
            self.focus_force()  # force focus to the window

    def _setup_ui(self, title: str, prompt: str):
        self.minsize(width=400, height=25)
        self.resizable(False, False)
        self.title(title)

        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.input_frame = ttk.Frame(self, padding=1, borderwidth=2)
        self.buttons_frame = ttk.Frame(self, padding=1, borderwidth=2)

        self.input_frame.grid(row=0, column=0, padx=4, pady=1, sticky="nswe")
        self.buttons_frame.grid(row=1, column=0, padx=4, pady=1, sticky="nswe")

        self._setup_input(prompt)
        self._setup_buttons()

    def _setup_input(self, prompt):
        self.input_frame.columnconfigure(1, weight=1)

        prompt_min_width = self.minsize()[0]
        prompt_label = ttk.Label(self.input_frame, text=prompt, wraplength=prompt_min_width)

        login_label = ttk.Label(self.input_frame, text="Login:")
        self.tk_var_login = tk.StringVar(value=self.login or "")
        self.login_entry = ttk.Entry(self.input_frame, textvariable=self.tk_var_login)

        pwd_label = ttk.Label(self.input_frame, text="Password:")
        self.tk_var_pwd = tk.StringVar(value=self.password or "")
        self.pwd_entry = ttk.Entry(self.input_frame, textvariable=self.tk_var_pwd)

        self.reveal = ttk.Button(self.input_frame, width=8)
        self.reveal.config(command=self.toggle_password)
        self.toggle_password()

        # position widget
        prompt_label.grid(row=0, column=0, columnspan=3, padx=2, pady=6, sticky="nswe")
        login_label.grid(row=1, column=0, padx=2, pady=4, ipady=4, sticky="nswe")
        self.login_entry.grid(row=1, column=1, columnspan=2, padx=2, pady=4, sticky="nswe")
        pwd_label.grid(row=2, column=0, padx=2, pady=4, ipady=4, sticky="nswe")
        self.pwd_entry.grid(row=2, column=1, columnspan=1, padx=2, pady=4, sticky="nswe")
        self.reveal.grid(row=2, column=2, columnspan=1, padx=2, pady=4, sticky="nswe")

        self.login_entry.focus_set()

    def _setup_position(self, parent: tk.Tk | tk.Toplevel | None):
        ui_utils.ui_center(self, parent)

    def _setup_buttons(self):
        self.buttons_frame.columnconfigure(0, weight=1)

        self.btn_ok = ttk.Button(self.buttons_frame, text="Ok", command=self.set_answer_and_close)
        self.btn_cancel = ttk.Button(self.buttons_frame, text="Annuler", command=self.close)

        self.btn_ok.grid(row=0, column=1, padx=2, pady=2, sticky="nse")
        self.btn_cancel.grid(row=0, column=2, padx=2, pady=2, sticky="nse")

    def _events_binds(self):
        self.login_entry.bind("<Return>", lambda _: self.set_answer_and_close())
        self.protocol("WM_DELETE_WINDOW", self.close)  # arrêter le programme quand fermeture de la fenêtre

    def toggle_password(self, hide_char: str = "\U000025cf"):
        ui_utils.toggle_password(self.reveal, self.pwd_entry, hide_char)
        self.pwd_entry.focus_set()

    def set_answer_and_close(self):
        self.answer = (self.tk_var_login.get(), self.tk_var_pwd.get())
        self.close()

    def close(self, _: Event | None = None):
        if self.parent:
            ui_utils.ui_undisable_parent(self, self.parent)
            self.parent.focus_set()
            self.destroy()  # doit se faire après avoir rendu le focus
        else:
            self.destroy()
            self.quit()


if __name__ == "__main__":
    answer = LoginDialog.ask("Login test", "Please enter your credential infos:")
    print(answer)
