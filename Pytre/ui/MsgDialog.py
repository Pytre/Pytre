import tkinter as tk
from tkinter import Event, ttk, font

if not __package__:
    import syspath_insert  # noqa: F401  # disable unused-import warning

from settings import get_app_path


class MsgDialog(tk.Toplevel):
    @classmethod
    def ask(cls, title: str, msg: str, buttons_txt: tuple, parent: tk.Tk | None = None) -> str | None:
        dialog = MsgDialog(title, msg, buttons_txt, parent=parent)
        cls.wait_window(dialog)
        return dialog.button_clicked

    def __init__(self, title: str, msg: str, buttons_txt: tuple, parent: tk.Tk | None = None):
        super().__init__()

        self.parent: tk.Tk = parent
        if not self.parent:
            self.master.withdraw()
        else:
            self.focus_set()
            self.parent.wm_attributes("-disabled", True)
            self.transient(self.parent)

        self.buttons = buttons_txt
        self.button_clicked = None

        self._setup_ui(title, msg)
        self._setup_position(self.parent)
        self._events_binds()

        if not self.parent:
            self.update_idletasks()  # get window on top
            self.focus_force()  # force focus to the window

    def _setup_ui(self, title: str, msg: str):
        minsize_width = max(275, font.Font().measure(title) + 125)
        self.minsize(width=minsize_width, height=125)
        self.resizable(False, False)
        self.title(title)

        self.msg_frame = ttk.Frame(self, padding=1, borderwidth=2)
        self.buttons_frame = ttk.Frame(self, padding=1, borderwidth=2)

        self.msg_frame.grid(row=0, column=0, padx=4, pady=1, sticky="nswe")
        self.buttons_frame.grid(row=1, column=0, padx=4, pady=1, sticky="nswe")

        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self._setup_msg(msg)
        self._setup_buttons()

    def _setup_position(self, parent: tk.Toplevel | None):
        self.update_idletasks()

        parent_x = parent.winfo_x() if parent else 0
        parent_y = parent.winfo_y() if parent else 0
        parent_width = parent.winfo_width() if parent else self.winfo_screenwidth()
        parent_height = parent.winfo_height() if parent else self.winfo_screenheight()

        x = int(parent_x + parent_width / 2 - self.winfo_width() / 2)
        y = int(parent_y + parent_height / 2 - self.winfo_height() / 2)

        self.geometry(f"+{x}+{y}")

    def _setup_msg(self, msg):
        icon_file = get_app_path() / "res" / "msg_question.png"
        icon_img = tk.PhotoImage(file=icon_file)

        label_icon = ttk.Label(self.msg_frame, image=icon_img, anchor=tk.CENTER)
        label_icon.image = icon_img

        wraplength = self.minsize()[0]
        label_msg = ttk.Label(self.msg_frame, text=msg, wraplength=wraplength)

        label_icon.grid(row=0, column=0, padx=5, pady=5, sticky="nswe")
        label_msg.grid(row=0, column=1, padx=5, pady=5, sticky="nswe")

        self.msg_frame.rowconfigure(0, weight=1)
        self.msg_frame.columnconfigure(1, weight=1)

    def _setup_buttons(self):
        for i, button in enumerate(self.buttons):
            btn = ttk.Button(self.buttons_frame, text=button, command=lambda b=button: self.on_click(b))
            btn.grid(row=0, column=i + 1, padx=2, pady=2, sticky="nswe")

        self.buttons_frame.columnconfigure(0, weight=1)

    def _events_binds(self):
        self.protocol("WM_DELETE_WINDOW", self.close)  # arrêter le programme quand fermeture de la fenêtre

    def on_click(self, button_clicked):
        self.button_clicked = button_clicked
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
    buttons = ("Ouvrir", "Enregistrer", "Annuler")
    answer = MsgDialog.ask("Fin execution", "Que voulez vous faire avec le fichier extrait ?", buttons)
    print(answer)
