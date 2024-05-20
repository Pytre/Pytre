import tkinter as tk
from tkinter import ttk, Event

from settings import get_app_path


class AboutWindow(tk.Toplevel):
    def __init__(self, parent=None):
        super().__init__()
        self.parent = parent
        if self.parent:
            self.focus_set()
            self.parent.wm_attributes("-disabled", True)
            self.transient(self.parent)
        else:
            self.master.withdraw()

        self._setup_ui()
        self._events_binds()

    # ------------------------------------------------------------------------------------------
    # Création de l'interface
    # ------------------------------------------------------------------------------------------
    def _setup_ui(self):
        self.title("Pytre - À propos")
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
        else:
            x = int(self.winfo_screenwidth() / 2 - 400 / 2)
            y = int(self.winfo_screenheight() / 2 - 350 / 1.8)

        self.geometry(f"+{x}+{y}")

    def _setup_top_frame(self):
        self.top_frame.rowconfigure(0, weight=1)
        self.top_frame.columnconfigure(1, weight=1)

        logo_file = get_app_path() / "res" / "app.gif"
        self.logo_img = tk.PhotoImage(file=logo_file)

        logo_widget = ttk.Label(self.top_frame, image=self.logo_img, justify="center")
        app_widget = ttk.Label(self.top_frame, text="Pytre", font=("TkDefaultFont", 20, "bold"), anchor="sw")
        author_widget = ttk.Label(
            self.top_frame,
            text="Copyright (C) 2021 / Created by Matthieu Ferrier",
            font=("TkDefaultFont", 8, "normal"),
            anchor="nw",
        )

        logo_widget.grid(row=0, column=0, rowspan=2, padx=10, pady=4, sticky="nswe")
        app_widget.grid(row=0, column=1, padx=4, pady=0, sticky="nswe")
        author_widget.grid(row=1, column=1, padx=4, pady=4, sticky="nwe")

    def _setup_license_frame(self):
        self.license_frame.rowconfigure(0, weight=1)
        self.license_frame.columnconfigure(0, weight=1)

        title = "GNU Affero General Public License"
        ttk.Style().configure("Bold.TLabelFrame.Label", font=("TkDefaultFont", 8, "bold"))
        title_label = ttk.Label(self.license_frame, text=title, style="Bold.TLabelFrame.Label")
        self.license_frame.config(labelwidget=title_label, borderwidth=2, labelanchor="n")

        license_file = get_app_path() / "app_about_license.txt"
        with open(license_file, "r") as file:
            license_txt = file.read()
        license_textbox = tk.Text(self.license_frame, wrap="word", font=("TkDefaultFont", 8, "normal"))

        license_textbox.insert("0.0", license_txt)
        license_textbox["state"] = "disabled"

        license_textbox.grid(row=0, column=0, padx=4, pady=4, sticky="nswe")

    def _setup_bottom_frame(self):
        self.bottom_frame.rowconfigure(0, weight=1)
        self.bottom_frame.columnconfigure(0, weight=1)
        self.bottom_frame.columnconfigure(2, weight=1)

        ok_btn = ttk.Button(self.bottom_frame, text="Ok", command=self.app_exit)
        ok_btn.grid(row=0, column=1, padx=4, pady=4, sticky="nswe")

    # ------------------------------------------------------------------------------------------
    # Définition des évènements générer par les traitements
    # ------------------------------------------------------------------------------------------
    def _events_binds(self):
        self.protocol("WM_DELETE_WINDOW", self.app_exit)  # arrêter le programme quand fermeture de la fenêtre

    # ------------------------------------------------------------------------------------------
    # Autres traitements
    # ------------------------------------------------------------------------------------------
    def app_exit(self, _: Event = None):
        if self.parent:
            self.parent.wm_attributes("-disabled", False)

        self.destroy()

        if self.parent is None:
            self.quit()


if __name__ == "__main__":
    my_app = AboutWindow()
    my_app.mainloop()
