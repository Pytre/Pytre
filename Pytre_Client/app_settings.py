from dataclasses import dataclass, field
from enum import Enum
from collections.abc import Callable

import tkinter as tk
from tkinter import ttk, Event, messagebox
from pathlib import Path

from Pytre_Core import utils
from Pytre_Core.settings import Settings
from Pytre_Core.user_prefs import CsvFormat, PrefEnum, UserPrefs
from Pytre_Core.about import APP_NAME

import Pytre_Client.ui_utils as ui_utils
from Pytre_Client.app_theme import set_theme


@dataclass(frozen=True)
class SetupInfos:
    title: str = f"{APP_NAME} - Paramètres"
    callbacks: tuple[Callable[[], None], ...] = field(default_factory=tuple)


class EntryType(Enum):
    TEXT = "text"
    CHECKBOX = "checkbox"


@dataclass(frozen=True)
class EntrySpec:
    text: str
    type: EntryType = EntryType.TEXT
    cmd: Callable[[], None] | None = None
    transform: Callable[[str | Path], str] | None = None


@dataclass(frozen=True)
class EntryWidgets:
    w_label: ttk.Label
    w_entry: ttk.Widget
    w_var: tk.Variable
    transform: Callable[[str | Path], str] | None


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

        self.entries: dict[str, EntryWidgets] = {}

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

    def _get_entries_spec(self) -> dict[str, EntrySpec]:
        """Override dans les classes enfants pour définir les informations de champs de paramètres"""
        return {}

    def _setup_entries(self):
        """création des contrôles de paramètres pour l'UI"""
        self.entries_frame.columnconfigure(1, weight=1)

        specs = self._get_entries_spec()

        num_row = 0
        for key, spec in specs.items():
            w_label = ttk.Label(self.entries_frame, text=spec.text + " : ")

            w_entry: ttk.Widget
            w_var: tk.Variable
            if spec.type == EntryType.CHECKBOX:
                w_var = tk.BooleanVar()
                w_entry = ttk.Checkbutton(self.entries_frame, variable=w_var, onvalue=True, offvalue=False)
                w_entry.configure(command=spec.cmd or "")
            else:
                w_var = tk.StringVar()
                w_entry = ttk.Entry(self.entries_frame, textvariable=w_var)

            self.entries[key] = EntryWidgets(w_label=w_label, w_entry=w_entry, w_var=w_var, transform=spec.transform)

            w_label.grid(row=num_row, column=0, padx=2, pady=2, sticky="nswe")
            w_entry.grid(row=num_row, column=1, columnspan=2, padx=2, pady=2, sticky="nswe")
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
    def __init__(self, settings: Settings, parent=None, on_close_callback=None):
        self.settings = settings
        self.settings.reload()
        super().__init__(parent=parent, on_close_callback=on_close_callback)

    # ------------------------------------------------------------------------------------------
    # Création de l'interface
    # ------------------------------------------------------------------------------------------
    def _setup_ui_infos(self) -> SetupInfos:
        return SetupInfos(
            title=f"{APP_NAME} - Paramètres généraux",
            callbacks=(self._set_logs_folder_state,),
        )

    def _get_entries_spec(self) -> dict[str, EntrySpec]:
        return {
            "queries_folder": EntrySpec(text="Dossier des requêtes", transform=self.normalize_path),
            "logs_are_on": EntrySpec(
                text="Activer les logs", type=EntryType.CHECKBOX, cmd=self._set_logs_folder_state
            ),
            "logs_folder": EntrySpec(text="Dossier des logs", transform=self.normalize_path),
            "settings_version": EntrySpec(text="Version des paramètres"),
        }

    def _set_logs_folder_state(self):
        state: bool = self.entries["logs_are_on"].w_var.get()
        folder_entry = self.entries["logs_folder"].w_entry

        assert isinstance(folder_entry, ttk.Entry)
        folder_entry["state"] = "normal" if state else "disabled"

    # ------------------------------------------------------------------------------------------
    # Autres traitements
    # ------------------------------------------------------------------------------------------
    def settings_load(self):
        for key, item in self.entries.items():
            val = getattr(self.settings, key)
            if item.transform:
                val = item.transform(val)
            item.w_var.set(val)

    def settings_save(self):
        for key, item in self.entries.items():
            val = item.w_var.get()
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
    def __init__(self, user_prefs: UserPrefs, parent=None, on_close_callback=None):
        self.user_prefs = user_prefs
        super().__init__(parent=parent, on_close_callback=on_close_callback)

    # ------------------------------------------------------------------------------------------
    # Création de l'interface
    # ------------------------------------------------------------------------------------------
    def _setup_ui_infos(self) -> SetupInfos:
        return SetupInfos(
            title=f"{APP_NAME} - Paramètres utilisateur",
            callbacks=tuple(),
        )

    def _get_entries_spec(self) -> dict[str, EntrySpec]:
        return {
            PrefEnum.DATE_FORMAT.json_name: EntrySpec(text="Format date"),
            PrefEnum.DECIMAL_SEPARATOR.json_name: EntrySpec(text="Séparateur décimal"),
            PrefEnum.CSV_DELIMITER.json_name: EntrySpec(text="CSV séparateur de champs"),
            PrefEnum.CSV_QUOTECHAR.json_name: EntrySpec(text="CSV identificateur de texte"),
            PrefEnum.CSV_ENCODING.json_name: EntrySpec(text="CSV format d'encodage"),
            PrefEnum.CACHE_DAYS.json_name: EntrySpec(text="Jours de cache à conserver"),
        }

    # ------------------------------------------------------------------------------------------
    # Autres traitements
    # ------------------------------------------------------------------------------------------
    def settings_load(self):
        prefs_dict = self.user_prefs.to_json()

        for key, item in self.entries.items():
            if key not in prefs_dict:
                continue

            val = prefs_dict[key]
            if item.transform:
                val = item.transform(val)
            item.w_var.set(val)

    def settings_save(self):
        delimiter = self.entries[PrefEnum.CSV_DELIMITER.json_name].w_var.get()
        quotechar = self.entries[PrefEnum.CSV_QUOTECHAR.json_name].w_var.get()
        try:
            CsvFormat.validate_dialect(delimiter, quotechar)
        except ValueError as e:
            messagebox.showerror(title="Mise à jour paramètres", message=str(e), parent=self)
            return

        for json_name, item in self.entries.items():
            val = item.w_var.get()

            try:
                pref_enum = PrefEnum.from_json_name(json_name)
                warning = self.user_prefs.set(pref_enum, val, False)
                if warning:
                    warning = f"Warning '{json_name}' :\n{warning}"
                    confirm = messagebox.askyesno(
                        title="Mise à jour paramètres", message=f"{warning}\n\nContinuer ?", parent=self
                    )
                    if not confirm:
                        return
            except KeyError:
                msg = f"Echec enregistrement, clé '{json_name}' non reconnue dans UserPrefsEnum"
                messagebox.showwarning(title="Mise à jour paramètres", message=msg, parent=self)
                return
            except (TypeError, ValueError, AttributeError) as e:
                msg = f"Echec enregistrement '{json_name}' :\n{e}"
                messagebox.showerror(title="Mise à jour paramètres", message=msg, parent=self)
                return

        if self.user_prefs.save():
            self.app_exit()
        else:
            msg = "Problème lors de l'enregistrement !"
            messagebox.showerror(title="Mise à jour paramètres", message=msg, parent=self)


if __name__ == "__main__":
    from Pytre_Core.kee import Kee

    kee = Kee()
    settings = Settings(kee)
    settings_app = SettingsWindow(settings)

    user_prefs = UserPrefs()
    user_app = UserPrefsWindow(user_prefs)

    settings_app.mainloop()
    user_app.mainloop()
