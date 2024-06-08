import tkinter as tk
from tkinter import Event, ttk, font


class InputDialog(tk.Toplevel):
    @classmethod
    def ask(cls, title, prompt: str, parent: tk.Tk | None = None) -> str | None:
        dialog = InputDialog(title, prompt, parent=parent)
        cls.wait_window(dialog)
        return dialog.answer

    def __init__(self, title, prompt: str, parent: tk.Tk | None = None):
        super().__init__()

        self.parent: tk.Tk = parent
        if not self.parent:
            self.master.withdraw()
        else:
            self.focus_set()
            self.parent.wm_attributes("-disabled", True)
            self.transient(self.parent)

        self.answer = None

        self._setup_ui(title, prompt)
        self._setup_position(self.parent)
        self._events_binds()

    def _setup_ui(self, title: str, prompt: str):
        minsize_width = max(250, font.Font().measure(title) + 125)
        self.minsize(width=minsize_width, height=25)
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

        minsize_width = self.minsize()[0]
        label = ttk.Label(self.input_frame, text=prompt, wraplength=minsize_width)
        self.tk_var_answer = tk.StringVar()
        self.entry = ttk.Entry(self.input_frame, textvariable=self.tk_var_answer)

        label.grid(row=0, column=0, padx=2, pady=2, sticky="nswe")
        self.entry.grid(row=1, column=0, columnspan=2, padx=2, pady=2, sticky="nswe")

        self.entry.focus_set()

    def _setup_position(self, parent: tk.Toplevel | None):
        self.update_idletasks()

        parent_x = parent.winfo_x() if parent else 0
        parent_y = parent.winfo_y() if parent else 0
        parent_width = parent.winfo_width() if parent else self.winfo_screenwidth()
        parent_height = parent.winfo_height() if parent else self.winfo_screenheight()

        x = int(parent_x + parent_width / 2 - self.winfo_width() / 2)
        y = int(parent_y + parent_height / 2 - self.winfo_height() / 2)

        self.geometry(f"+{x}+{y}")

    def _setup_buttons(self):
        self.buttons_frame.columnconfigure(0, weight=1)

        self.btn_ok = ttk.Button(self.buttons_frame, text="Ok", command=self.set_answer_and_close)
        self.btn_cancel = ttk.Button(self.buttons_frame, text="Annuler", command=self.close)

        self.btn_ok.grid(row=0, column=1, padx=2, pady=2, sticky="nse")
        self.btn_cancel.grid(row=0, column=2, padx=2, pady=2, sticky="nse")

    def _events_binds(self):
        self.entry.bind("<Return>", lambda _: self.set_answer_and_close())
        self.protocol("WM_DELETE_WINDOW", self.close)  # arrêter le programme quand fermeture de la fenêtre

    def set_answer_and_close(self):
        self.answer = self.tk_var_answer.get()
        self.close()

    def close(self, _: Event = None):
        if self.parent:
            self.parent.wm_attributes("-disabled", False)
            self.parent.focus_set()
            self.destroy()  # doit se faire après avoir rendu le focus
        else:
            self.destroy()
            self.quit()


if __name__ == "__main__":
    answer = InputDialog.ask("Test input", "Entrer une valeur :")
    print(answer)
