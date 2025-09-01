import tkinter as tk
from tkinter import Event, ttk, font

if not __package__:
    import syspath_insert  # noqa: F401  # disable unused-import warning

import utils
from settings import get_app_path
from ui.app_theme import set_theme


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
            utils.ui_disable_parent(self, self.parent)
            self.transient(self.parent)

        self.buttons_txt = buttons_txt
        self.buttons_widgets: list[tk.Widget] = []
        self.button_clicked = None

        set_theme(self)
        self._setup_ui(title, msg)
        self._setup_position(self.parent)
        self._events_binds()

        self.update_idletasks()  # get window on top
        self.focus_force()  # force focus to the window

    def _setup_ui(self, title: str, msg: str):
        minsize_width = max(300, font.Font().measure(title) + 125)
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
        utils.ui_center(self, parent)

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
        for i, btn_text in enumerate(self.buttons_txt, 1):
            btn = ttk.Button(self.buttons_frame, text=btn_text, command=lambda txt=btn_text: self.on_click(txt))
            btn.grid(row=0, column=i, padx=2, pady=2, sticky="nswe")
            self.buttons_widgets.append(btn)

        self.buttons_frame.columnconfigure(0, weight=1)

    def _events_binds(self):
        self.bind("<Left>", lambda _: self.select_btn_move(-1))
        self.bind("<Right>", lambda _: self.select_btn_move(1))
        self.bind("<Return>", lambda _: self.on_click(None))
        self.bind("<Escape>", self.close)
        self.protocol("WM_DELETE_WINDOW", self.close)  # arrêter le programme quand fermeture de la fenêtre

    def select_btn_move(self, move: int):
        widget_with_focus = self.focus_displayof()

        # détermination de la position du bouton à sélectionner
        if widget_with_focus in self.buttons_widgets:
            pos = self.buttons_widgets.index(widget_with_focus) + move
        elif move < 0:
            pos = len(self.buttons_widgets) - 1
        else:
            pos = 0

        # gestion de la position si en dehors de la borne début / fin
        if pos >= len(self.buttons_widgets):
            pos = 0
        if pos < 0:
            pos = len(self.buttons_widgets) - 1

        self.buttons_widgets[pos].focus_set()

    def on_click(self, button_text):
        if button_text is None:
            widget_with_focus = self.focus_displayof()
            if widget_with_focus not in self.buttons_widgets:
                return
            elif button_text is None:
                button_text = widget_with_focus["text"]

        self.button_clicked = button_text
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
    buttons = ("Ouvrir", "Enregistrer", "Annuler")
    answer = MsgDialog.ask(
        "Fin execution",
        "Le fichier extrait a été enregisté.\nVoulez-vous l'ouvrir ou enregistrer une copie ailleurs ?",
        buttons,
    )
    print(answer)
