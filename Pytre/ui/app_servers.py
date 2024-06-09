import tkinter as tk
from tkinter import ttk, Event, messagebox

if not __package__:
    import syspath_insert  # noqa: F401  # disable unused-import warning

from settings import Server
from about import APP_NAME


class ServersWindow(tk.Toplevel):
    def __init__(self, parent=None):
        super().__init__()
        self.parent = parent
        if self.parent:
            self.focus_set()
            self.parent.wm_attributes("-disabled", True)
            self.transient(self.parent)
        else:
            self.master.withdraw()

        self.server = Server()

        self._setup_ui()
        self._events_binds()

    # ------------------------------------------------------------------------------------------
    # Création de l'interface
    # ------------------------------------------------------------------------------------------
    def _setup_ui(self):
        self.title(f"{APP_NAME} - Paramètres Serveur")

        self.minsize(width=400, height=100)
        if self.parent:
            self.geometry(f"+{self.parent.winfo_x() + 200}+{self.parent.winfo_y() + 150}")
        else:
            self.geometry("+200+150")
        self.resizable(True, True)

        self.entries_frame = ttk.Frame(self, padding=1, borderwidth=2)
        self.buttons_frame = ttk.Frame(self, padding=1, borderwidth=2)

        self.entries_frame.grid(row=0, column=0, padx=4, pady=4, sticky="nswe")
        self.buttons_frame.grid(row=1, column=0, padx=4, pady=4, sticky="nswe")

        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self._setup_entries()
        self._setup_buttons()

    def _setup_entries(self):
        self.entries_frame.columnconfigure(1, weight=1)

        self.entries = {}
        num_row = 0
        for key, item in self.server.to_dict().items():
            if key == "title":  # ne pas afficher le titre, il ne doit pas etre modifier
                continue

            my_label = ttk.Label(self.entries_frame, text=key + " : ")
            my_tk_var = tk.StringVar(value=item)
            my_entry = ttk.Entry(self.entries_frame, textvariable=my_tk_var)

            self.entries[key] = {"w_label": my_label, "w_entry": my_entry, "var": my_tk_var}

            my_label.grid(row=num_row, column=0, padx=2, pady=2, sticky="nswe")

            if key == "password":
                my_entry.grid(row=num_row, column=1, padx=2, pady=2, sticky="nswe")
                reveal_button = ttk.Button(self.entries_frame, width=8)
                reveal_button.config(command=lambda wc=reveal_button, wt=my_entry: self.toggle_password(wc, wt))
                self.toggle_password(reveal_button, my_entry)
                reveal_button.grid(row=num_row, column=2, padx=2, pady=2, sticky="nswe")
            else:
                my_entry.grid(row=num_row, column=1, columnspan=2, padx=2, pady=2, sticky="nswe")

            num_row += 1

    def _setup_buttons(self):
        self.buttons_frame.columnconfigure(0, weight=1)

        self.btn_save = ttk.Button(self.buttons_frame, text="Enregistrer", command=self.server_save)
        self.btn_cancel = ttk.Button(self.buttons_frame, text="Annuler", command=self.app_exit)

        self.btn_save.grid(row=0, column=1, padx=2, pady=2, sticky="nse")
        self.btn_cancel.grid(row=0, column=2, padx=2, pady=2, sticky="nse")

    # ------------------------------------------------------------------------------------------
    # Définition des évènements générer par les traitements
    # ------------------------------------------------------------------------------------------
    def _events_binds(self):
        self.protocol("WM_DELETE_WINDOW", self.app_exit)  # arrêter le programme quand fermeture de la fenêtre

    # ------------------------------------------------------------------------------------------
    # Autres traitements
    # ------------------------------------------------------------------------------------------
    def toggle_password(self, w_caller: tk.Widget, w_target: tk.Widget, hide_char: str = "\U000025CF"):
        if w_target["show"] == "":
            w_caller.config(text="Voir")
            w_target.config(show=hide_char)
        else:
            w_caller.config(text="Masquer")
            w_target.config(show="")

    def server_save(self):
        for key in self.entries.keys():
            val = self.entries[key]["var"].get()
            setattr(self.server, key, val)

        result = self.server.save()
        if result:
            self.app_exit()
        else:
            msg = "Problème lors de l'enregistrement !"
            messagebox.showerror(title="Mise à jour infos serveur", message=msg, parent=self, type=messagebox.OK)

    def app_exit(self, _: Event = None):
        if self.parent:
            self.parent.wm_attributes("-disabled", False)

        self.destroy()

        if self.parent is None:
            self.quit()


if __name__ == "__main__":
    my_app = ServersWindow()
    my_app.mainloop()
