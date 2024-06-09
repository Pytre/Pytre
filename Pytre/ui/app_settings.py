import tkinter as tk
from tkinter import ttk, Event, messagebox

if not __package__:
    import syspath_insert  # noqa: F401  # disable unused-import warning

from settings import Settings
from about import APP_NAME


class SettingsWindow(tk.Toplevel):
    def __init__(self, parent=None):
        super().__init__()
        self.parent = parent
        if self.parent:
            self.focus_set()
            self.parent.wm_attributes("-disabled", True)
            self.transient(self.parent)
        else:
            self.master.withdraw()

        self.settings = Settings()

        self._setup_ui()
        self._events_binds()

    # ------------------------------------------------------------------------------------------
    # Création de l'interface
    # ------------------------------------------------------------------------------------------
    def _setup_ui(self):
        self.title(f"{APP_NAME} - Paramètres généraux")

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
        self.settings_load()

    def _setup_entries(self):
        self.entries_frame.columnconfigure(1, weight=1)

        self.entries = {
            "field_separator": {"text": "Séparateur de champs"},
            "decimal_separator": {"text": "Séparateur décimal"},
            "date_format": {"text": "Format date"},
            "queries_folder": {"text": "Dossier des requêtes"},
            "settings_version": {"text": "Version des paramètres"},
        }

        num_row = 0
        for _, item in self.entries.items():
            my_label = ttk.Label(self.entries_frame, text=item["text"] + " : ")
            my_tk_var = tk.StringVar()
            my_entry = ttk.Entry(self.entries_frame, textvariable=my_tk_var)

            new_keys = {"w_label": my_label, "w_entry": my_entry, "var": my_tk_var}
            item.update(new_keys)

            my_label.grid(row=num_row, column=0, padx=2, pady=2, sticky="nswe")
            my_entry.grid(row=num_row, column=1, columnspan=2, padx=2, pady=2, sticky="nswe")

            num_row += 1

    def _setup_buttons(self):
        self.buttons_frame.columnconfigure(0, weight=1)

        self.btn_save = ttk.Button(self.buttons_frame, text="Enregistrer", command=self.settings_save)
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
    def settings_load(self):
        for key, item in self.entries.items():
            val = getattr(self.settings, key)
            item["var"].set(val)

    def settings_save(self):
        for key, item in self.entries.items():
            val = item["var"].get()
            setattr(self.settings, key, val)

        result = self.settings.save()
        if result:
            self.app_exit()
        else:
            msg = "Problème lors de l'enregistrement !"
            messagebox.showerror(title="Mise à jour paramètres", message=msg, parent=self, type=messagebox.OK)

    def app_exit(self, _: Event = None):
        if self.parent:
            self.parent.wm_attributes("-disabled", False)

        self.destroy()

        if self.parent is None:
            self.quit()


if __name__ == "__main__":
    my_app = SettingsWindow()
    my_app.mainloop()
