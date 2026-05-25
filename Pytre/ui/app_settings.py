from dataclasses import dataclass, field
from collections.abc import Callable

import tkinter as tk
from tkinter import ttk, Event, messagebox
from pathlib import Path

if not __package__:
    import syspath_insert  # noqa: F401  # disable unused-import warning

import utils
from settings import Settings
from user_prefs import UserPrefs, PrefsEnum
from about import APP_NAME

import ui.ui_utils as ui_utils
from ui.app_theme import set_theme


@dataclass(frozen=True)
class SetupInfos:
    title: str = f"{APP_NAME} - Paramètres"
    callbacks: tuple[Callable[[], None], ...] = field(default_factory=tuple)


class MainSettingsWindow(tk.Toplevel):
    def __init__(self, parent=None, on_close_callback=None):
        super().__init__()
        self.parent = parent
        self.on_close_callback = on_close_callback
        if self.parent:
            self.focus_set()
            ui_utils.ui_disable_parent(self, self.parent)
            self.transient(self.parent)
        else:
            assert isinstance(self.master, tk.Tk)
            self.master.withdraw()

        self.entries: dict[str, dict] = {}

        self.settings = Settings()
        self.settings.reload()

        set_theme(self)
        self._setup_ui()
        self._events_binds()

    # ------------------------------------------------------------------------------------------
    # Création de l'interface
    # ------------------------------------------------------------------------------------------
    def _setup_ui_infos(self) -> SetupInfos:
        """Override dans les classes enfants pour info à utiliser par setup_ui"""
        return SetupInfos(
            title=f"{APP_NAME} - Paramètres",
            callbacks=tuple(),
        )

    def _setup_ui(self):
        setup_infos = self._setup_ui_infos()

        self.title(setup_infos.title)

        self.minsize(width=400, height=100)
        if self.parent:
            self.geometry(f"+{self.parent.winfo_x() + 200}+{self.parent.winfo_y() + 150}")
        elif utils.get_system() == "Windows":
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

        if setup_infos.callbacks:
            for func in setup_infos.callbacks:
                func()

    def _get_entries(self) -> dict[str, dict]:
        """Override dans les classes enfants pour définir les informations de champs de paramètres"""
        return {}

    def _setup_entries(self):
        """Override dans les classes enfants pour créer les contrôles de paramètres pour l'UI"""
        pass

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
    def normalize_path(self, val: str | Path) -> str:
        return str(Path(val)) if val else ""

    def settings_load(self):
        """Override dans les classes enfants pour alimenter les contrôles"""
        pass

    def settings_save(self):
        """Override dans les classes enfants pour sauvegarder les infos des contrôles"""
        pass

    def app_exit(self, _: Event | None = None):
        if self.on_close_callback:
            self.on_close_callback()

        if self.parent:
            ui_utils.ui_undisable_parent(self, self.parent)

        self.destroy()

        if self.parent is None:
            self.quit()


class SettingsWindow(MainSettingsWindow):
    def __init__(self, parent=None, on_close_callback=None):
        super().__init__(parent=parent, on_close_callback=on_close_callback)

    # ------------------------------------------------------------------------------------------
    # Création de l'interface
    # ------------------------------------------------------------------------------------------
    def _setup_ui_infos(self) -> SetupInfos:
        return SetupInfos(
            title=f"{APP_NAME} - Paramètres généraux",
            callbacks=(self._set_logs_folder_state,),
        )

    def _get_entries(self) -> dict[str, dict]:
        return {
            "field_separator": {"text": "Séparateur de champs"},
            "decimal_separator": {"text": "Séparateur décimal"},
            "date_format": {"text": "Format date"},
            "extract_encoding": {"text": "Encoding format"},
            "queries_folder": {"text": "Dossier des requêtes", "transform": self.normalize_path},
            "logs_are_on": {"text": "Activer les logs"},
            "logs_folder": {"text": "Dossier des logs", "transform": self.normalize_path},
            "users_cache_expiration": {"text": "Jours de cache utilisateurs"},
            "settings_version": {"text": "Version des paramètres"},
        }

    def _setup_entries(self):
        self.entries_frame.columnconfigure(1, weight=1)

        self.entries = self._get_entries()

        num_row = 0
        for key, item in self.entries.items():
            my_label = ttk.Label(self.entries_frame, text=item["text"] + " : ")

            if key == "logs_are_on":
                my_tk_var = tk.BooleanVar()
                my_entry = ttk.Checkbutton(self.entries_frame, variable=my_tk_var, onvalue=True, offvalue=False)
                my_entry["command"] = self._set_logs_folder_state
            else:
                my_tk_var = tk.StringVar()
                my_entry = ttk.Entry(self.entries_frame, textvariable=my_tk_var)

            new_keys = {"w_label": my_label, "w_entry": my_entry, "var": my_tk_var}
            item.update(new_keys)

            my_label.grid(row=num_row, column=0, padx=2, pady=2, sticky="nswe")
            my_entry.grid(row=num_row, column=1, columnspan=2, padx=2, pady=2, sticky="nswe")

            num_row += 1

    def _set_logs_folder_state(self):
        state: bool = self.entries["logs_are_on"]["var"].get()
        folder_entry: ttk.Entry = self.entries["logs_folder"]["w_entry"]

        folder_entry["state"] = "normal" if state else "disabled"

    # ------------------------------------------------------------------------------------------
    # Autres traitements
    # ------------------------------------------------------------------------------------------
    def settings_load(self):
        for key, item in self.entries.items():
            val = getattr(self.settings, key)
            transform = item.get("transform")
            if transform:
                val = transform(val)
            item["var"].set(val)

    def settings_save(self):
        for key, item in self.entries.items():
            val = item["var"].get()
            setattr(self.settings, key, val)

        try:
            result = self.settings.save()
        except (LookupError, ValueError) as e:
            messagebox.showerror(title="Mise à jour paramètres", message=str(e), parent=self)
            return

        if result:
            self.app_exit()
        else:
            msg = "Problème lors de l'enregistrement !"
            messagebox.showerror(title="Mise à jour paramètres", message=msg, parent=self)


class UserPrefsWindow(MainSettingsWindow):
    def __init__(self, parent=None, on_close_callback=None):
        self.user_prefs = UserPrefs()
        super().__init__(parent=parent, on_close_callback=on_close_callback)

    # ------------------------------------------------------------------------------------------
    # Création de l'interface
    # ------------------------------------------------------------------------------------------
    def _setup_ui_infos(self) -> SetupInfos:
        return SetupInfos(
            title=f"{APP_NAME} - Paramètres utilisateur",
            callbacks=tuple(),
        )

    def _get_entries(self) -> dict[str, dict]:
        return {
            PrefsEnum.cache_folder.name: {
                "text": "Dossier cache temporaire",
                "transform": self.normalize_path,
            },
        }

    def _setup_entries(self):
        self.entries_frame.columnconfigure(1, weight=1)

        self.entries = self._get_entries()

        num_row = 0
        for key, item in self.entries.items():
            my_label = ttk.Label(self.entries_frame, text=item["text"] + " : ")

            my_tk_var = tk.StringVar()
            my_entry = ttk.Entry(self.entries_frame, textvariable=my_tk_var)

            new_keys = {"w_label": my_label, "w_entry": my_entry, "var": my_tk_var}
            item.update(new_keys)

            my_label.grid(row=num_row, column=0, padx=2, pady=2, sticky="nswe")
            my_entry.grid(row=num_row, column=1, columnspan=2, padx=2, pady=2, sticky="nswe")

            num_row += 1

    # ------------------------------------------------------------------------------------------
    # Autres traitements
    # ------------------------------------------------------------------------------------------
    def settings_load(self):
        prefs_dict = self.user_prefs.to_json()

        for key, item in self.entries.items():
            if key not in prefs_dict:
                continue

            val = prefs_dict.get(key)
            transform = item.get("transform")
            if transform:
                val = transform(val)
            item["var"].set(val)

    def settings_save(self):
        for key, item in self.entries.items():
            val = item["var"].get()

            try:
                enum_key = PrefsEnum[key]
                warning = self.user_prefs.set(enum_key, val, False)
                if warning:
                    warning = f"Warning '{key}' :\n{warning}"
                    confirm = messagebox.askyesno(
                        title="Mise à jour paramètres", message=f"{warning}\n\nContinuer ?", parent=self
                    )
                    if not confirm:
                        return
            except KeyError:
                msg = f"Echec enregistrement, clé '{key}' non reconnue dans UserPrefsEnum"
                messagebox.showwarning(title="Mise à jour paramètres", message=msg, parent=self)
                return
            except (TypeError, ValueError, AttributeError) as e:
                msg = f"Echec enregistrement '{key}' :\n{e}"
                messagebox.showerror(title="Mise à jour paramètres", message=msg, parent=self)
                return

        if self.user_prefs.save():
            self.app_exit()
        else:
            msg = "Problème lors de l'enregistrement !"
            messagebox.showerror(title="Mise à jour paramètres", message=msg, parent=self)


if __name__ == "__main__":
    # my_app = SettingsWindow()
    my_app = UserPrefsWindow()
    my_app.mainloop()
