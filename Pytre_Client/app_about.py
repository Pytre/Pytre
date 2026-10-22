import tkinter as tk
import webbrowser
from tkinter import ttk, Event, font

from Pytre_Core import about
from Pytre_Core.settings import get_app_path
from Pytre_Core.updater import Updater
from Pytre_Core.user_prefs import PrefEnum, UserPrefs
from Pytre_Core.utils import get_system

import Pytre_Client.ui_utils as ui_utils
from Pytre_Client.app_theme import set_theme


class AboutWindow(tk.Toplevel):
    def __init__(self, parent: tk.Toplevel | None = None, user_prefs: UserPrefs | None = None):
        super().__init__()
        self.parent: tk.Toplevel | None = parent
        if self.parent:
            self.focus_set()
            ui_utils.ui_disable_parent(self, self.parent)
            self.transient(self.parent)
        else:
            assert isinstance(self.master, tk.Tk)
            self.master.withdraw()

        self.user_prefs: UserPrefs = user_prefs or UserPrefs()

        self.updatable: bool = Updater.is_updatable()
        self.update_check: bool = self.user_prefs.get(PrefEnum.UPDATE_CHECK)
        self.update_channel: about.AppStage = self.user_prefs.get(PrefEnum.UPDATE_CHANNEL)
        self.channels: list[str] = [stage.name for stage in sorted(about.AppStage, reverse=True)]

        set_theme(self)
        self._setup_ui()
        self._events_binds()

    # ------------------------------------------------------------------------------------------
    # Création de l'interface
    # ------------------------------------------------------------------------------------------
    def _setup_ui(self):
        self.title(f"{about.APP_NAME} - À propos")
        self.geometry("400x350")
        self._setup_position()
        self.resizable(False, False)

        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

        self.top_frame = ttk.Frame(self)
        self.license_frame = ttk.LabelFrame(self)
        self.bottom_frame = ttk.Frame(self)

        self.top_frame.grid(row=0, column=0, padx=5, pady=10, sticky="nswe")
        self.license_frame.grid(row=1, column=0, padx=5, pady=0, sticky="nswe")
        self.bottom_frame.grid(row=2, column=0, padx=5, pady=10, sticky="nswe")

        self._setup_top_frame()
        self._setup_license_frame()
        self._setup_bottom_frame()

    def _setup_position(self):
        if self.parent:
            x = int(self.parent.winfo_x() + self.parent.winfo_width() / 2 - 400 / 2)
            y = int(self.parent.winfo_y() + self.parent.winfo_height() / 2 - 350 / 2)
            self.geometry(f"+{x}+{y}")
        elif get_system() == "Windows":
            x = int(self.winfo_screenwidth() / 2 - 400 / 2)
            y = int(self.winfo_screenheight() / 2 - 350 / 1.8)
            self.geometry(f"+{x}+{y}")

    def _setup_top_frame(self):
        self.top_frame.rowconfigure(0, weight=1)
        self.top_frame.columnconfigure(2, weight=1)

        logo_file = get_app_path() / "res" / "app.gif"
        self.logo_img = tk.PhotoImage(file=logo_file)

        logo_label = ttk.Label(self.top_frame, image=self.logo_img, justify="center")
        app_label = ttk.Label(self.top_frame, text=about.APP_NAME, font=("TkDefaultFont", 20, "bold"), anchor="sw")
        app_version = about.full_version()
        version_label = ttk.Label(
            self.top_frame,
            text=f"Version : {app_version}",
            font=("TkDefaultFont", 8, "normal"),
            anchor="ne",
        )
        author_label = ttk.Label(
            self.top_frame,
            text=f"Copyright (C) {about.COPYRIGHT_YEAR} / Created by {about.AUTHOR}",
            font=("TkDefaultFont", 8, "normal"),
            anchor="nw",
        )
        link_label = ttk.Label(
            self.top_frame,
            text=about.HOMEPAGE_LINK,
            foreground="blue",
            font=("TkDefaultFont", 8, "underline"),
            cursor="hand2",
        )

        link_label.bind("<Button-1>", lambda e: webbrowser.open_new_tab(about.HOMEPAGE_LINK))

        logo_label.grid(row=0, column=0, rowspan=3, padx=10, pady=0, sticky="nswe")
        app_label.grid(row=0, column=1, padx=4, pady=0, sticky="nswe")
        version_label.grid(row=0, column=2, padx=4, pady=0, sticky="ne")
        author_label.grid(row=1, column=1, columnspan=2, padx=4, pady=0, sticky="swe")
        link_label.grid(row=2, column=1, columnspan=2, padx=4, pady=0, sticky="nwe")

    def _setup_license_frame(self):
        self.license_frame.rowconfigure(0, weight=1)
        self.license_frame.columnconfigure(0, weight=1)

        default_font = font.nametofont("TkDefaultFont")
        font_family = default_font.actual("family")
        font_size = default_font.actual("size") - 1
        labelframe = "Header.TLabelframe.Label"
        ttk.Style(self).configure(labelframe, font=(font_family, font_size, "bold"))

        title_label = ttk.Label(self.license_frame, text=about.LICENSE_NAME, style=labelframe)
        self.license_frame.config(labelwidget=title_label, borderwidth=2, labelanchor="n")

        license_textbox = tk.Text(self.license_frame, wrap="word", font=("TkDefaultFont", 8, "normal"))
        license_textbox.insert("0.0", about.get_license_text())
        license_textbox["state"] = "disabled"

        license_textbox.grid(row=0, column=0, padx=4, pady=4, sticky="nswe")

    def _setup_bottom_frame(self):
        self.bottom_frame.rowconfigure(0, weight=1)
        self.bottom_frame.columnconfigure(0, weight=0)
        self.bottom_frame.columnconfigure(1, weight=1)
        self.bottom_frame.columnconfigure(2, weight=0)

        if self.updatable:
            self.upd_checkbox_var = tk.BooleanVar(value=self.update_check)
            upd_checkbox = ttk.Checkbutton(self.bottom_frame, text="Mise à jour auto, canal : ")
            upd_checkbox.configure(variable=self.upd_checkbox_var)

            self.upd_combo = ttk.Combobox(self.bottom_frame, state="readonly", values=self.channels)
            self.upd_combo.set(self.update_channel.name)

            upd_checkbox.grid(row=0, column=0, padx=4, pady=4, sticky="nswe")
            self.upd_combo.grid(row=0, column=1, padx=4, pady=4, sticky="nswe")

        ok_btn = ttk.Button(self.bottom_frame, text="Ok", command=self.app_exit)
        ok_btn.grid(row=0, column=2, padx=4, pady=4, sticky="nswe")

    # ------------------------------------------------------------------------------------------
    # Définition des évènements générer par les traitements
    # ------------------------------------------------------------------------------------------
    def _events_binds(self):
        self.protocol("WM_DELETE_WINDOW", self.app_exit)  # arrêter le programme quand fermeture de la fenêtre

    # ------------------------------------------------------------------------------------------
    # Autres traitements
    # ------------------------------------------------------------------------------------------
    def save_channel(self):
        changed: bool = False

        # update_check
        selected_check = self.upd_checkbox_var.get()
        if selected_check != self.update_check:
            self.user_prefs.set(PrefEnum.UPDATE_CHECK, selected_check, save=False)
            changed = True

        # update_channel
        try:
            selected_channel = about.AppStage.from_value(self.upd_combo.get())
        except ValueError:
            selected_channel = None

        if selected_channel and selected_channel != self.update_channel:
            self.user_prefs.set(PrefEnum.UPDATE_CHANNEL, selected_channel, save=False)
            changed = True

        if changed:
            self.user_prefs.save()

    def app_exit(self, _: Event | None = None):
        if self.updatable:
            self.save_channel()

        if self.parent:
            ui_utils.ui_undisable_parent(self, self.parent)

        self.destroy()

        if self.parent is None:
            self.quit()


if __name__ == "__main__":
    my_app = AboutWindow()
    my_app.mainloop()
