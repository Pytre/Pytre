import time
import subprocess
import tkinter as tk
from tkinter import Event, ttk, messagebox
from os import startfile
from threading import Thread

import utils
import sql_query
from app_debug import DebugWindow
from app_users import UsersWindow
from app_servers import ServersWindow
from app_settings import SettingsWindow
from app_password import PasswordWindow
from app_about import AboutWindow

SETTINGS = sql_query.SETTINGS
PYTRE_VERSION = "1.031"


class App(tk.Tk):
    def __init__(self, queries_folder=""):
        super().__init__()

        self.user: sql_query.settings.User = SETTINGS.curr_user
        self.queries: list[sql_query.Query] = []
        self.query: sql_query.Query = sql_query.Query()
        self.params_widgets: dict[str, ttk.Widget] = {}

        self.queries_folder = SETTINGS.queries_folder if queries_folder == "" else queries_folder

        self.setup_style()
        self.setup_ui()
        self.setup_events_binds()

        self.is_authorized = True
        if not self.check_user_access() or not self.check_min_version():
            self.is_authorized = False
            return

        self.refresh_queries()

        if self.user.msg_login:
            self.output_msg(str(self.user.msg_login) + "\n", "1.0", "1.0")

        self.extract_folder_cleaning()

    def check_user_access(self) -> bool:
        if not self.user.is_authorized:
            messagebox.showerror(
                "Erreur",
                "Vous n'êtes pas dans liste des utilisateurs autorisées !"
                + "\nDonnées d'identification :"
                + f"\n- User : {self.user.username}",
            )
            return False
        else:
            if not self.user.admin:
                self.queries_btn_folder.grid_forget()
            return True

    def check_min_version(self) -> bool:
        if SETTINGS.min_version_settings > SETTINGS.settings_version:
            messagebox.showerror(
                "Version settings.db",
                "Le fichier settings.db utilisé n'est pas à jour."
                f"\n\n- Version utilisée : {SETTINGS.settings_version}"
                f"\n- Version mini : {SETTINGS.min_version_settings}"
                "\n\nMerci d'utiliser le fichier des settings à jour",
            )
            return False

        if SETTINGS.min_version_pytre > PYTRE_VERSION:
            messagebox.showerror(
                "Version Pytre",
                "Votre version de Pytre n'est pas à jour."
                f"\n\n- Version utilisée : {PYTRE_VERSION}"
                f"\n- Version mini : {SETTINGS.min_version_pytre}"
                "\n\nMerci d'utiliser une version à jour",
            )
            return False

        return True

    def extract_folder_cleaning(self):
        extract_folder = SETTINGS.extract_folder

        files = utils.old_files_list(extract_folder)  # liste des fichiers à supprimer
        files_nb = len(files)
        if files_nb:
            files_size = round(sum([size.stat().st_size for size in files]) / 1024**2, 2)
            files_date = utils.most_recent_files(files)

            answer = messagebox.askyesno(
                "Suppression des anciennes extractions",
                f"Dans le dossier des extractions il existe {files_nb} fichiers "
                + f"datant d'avant le {files_date.strftime('%d/%m/%Y')}. "
                + f"Ils vont être supprimés pour libérer {files_size} Mo d'espace disque.\n\n"
                + "Vous pouvez choisir de ne pas les supprimer mais ce message reviendra à chaque ouverture.\n\n"
                + "Si des fichiers doivent être conservés cliquer sur non et changer les de répertoire "
                + "ou déplacer les dans un sous-répertoire.",
                icon="warning",
            )

            if answer:
                utils.old_files_delete(files)
            else:
                self.open_folder(extract_folder)

    # ------------------------------------------------------------------------------------------
    # Définition des styles
    # ------------------------------------------------------------------------------------------
    def setup_style(self):
        self.style_frame_label = "Bold.TLabelFrame.Label"
        ttk.Style(self).configure(self.style_frame_label, font=("TkDefaultFont", 10, "bold"))

    # ------------------------------------------------------------------------------------------
    # Création de l'interface
    # ------------------------------------------------------------------------------------------
    def setup_ui(self):
        self.title(f"Pytre - V.{PYTRE_VERSION}")
        icon_file = SETTINGS.app_path / "res" / "app.ico"
        self.iconbitmap(default=icon_file)

        self.minsize(width=975, height=700)
        self.resizable(True, True)

        self.setup_ui_menu()
        self.setup_ui_paned_window()
        self.setup_ui_left_frame()
        self.setup_ui_right_frame()

        self.paned_window.add(self.left_frame, weight=0)
        self.paned_window.add(self.right_frame, weight=1)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.position()

    def setup_ui_menu(self):
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        self.menu_query = tk.Menu(menubar, tearoff=False)
        self.menu_query.add_command(label="Executer", state="disabled", command=self.execute_query)
        self.menu_query.add_command(label="Extractions...", command=lambda: self.open_folder(SETTINGS.extract_folder))
        self.menu_query.add_command(label="Debug...", state="disabled", command=self.debug_query)
        self.menu_query.add_separator()
        self.menu_query.add_command(label="Recharger", command=lambda: self.refresh_queries())
        menubar.add_cascade(label="Requêtes", menu=self.menu_query)

        if self.user.admin:
            menu_admin = tk.Menu(menubar, tearoff=False)
            menu_admin.add_command(label="Utilisateurs...", command=self.manage_users)
            menu_admin.add_command(label="Serveur...", command=self.manage_servers)
            menu_admin.add_command(label="Paramètres généraux...", command=self.manage_settings)
            menu_admin.add_separator()
            menu_admin.add_command(label="Paramètres, mot de passe...", command=self.manage_password)
            menubar.add_cascade(label="Administation", menu=menu_admin)

        menu_about = tk.Menu(menubar, tearoff=False)
        menu_about.add_command(label="À propos de Pytre...", command=self.about_info)
        menubar.add_cascade(label="?", menu=menu_about)

    def setup_ui_paned_window(self):
        self.paned_window = ttk.PanedWindow(self, orient="horizontal")
        self.paned_window.grid(row=0, column=0, padx=5, pady=5, sticky="nswe")

    def setup_ui_left_frame(self):
        self.left_frame = ttk.Frame(self.paned_window, padding=1, borderwidth=2)
        self.left_frame.grid(row=0, column=0, padx=0, pady=0, sticky="nswe")

        self.queries_filter_text = tk.StringVar()

        self.queries_label_filter = ttk.Label(self.left_frame, text="Filtre :", justify=tk.LEFT)
        self.queries_entry_filter = ttk.Entry(self.left_frame, textvariable=self.queries_filter_text, width=30)
        self.queries_btn_folder = ttk.Button(
            self.left_frame,
            text="Dossier",
            command=lambda: self.open_folder(SETTINGS.queries_folder),
        )
        self.queries_btn_refresh = ttk.Button(
            self.left_frame,
            text="Réinitialiser",
            command=lambda: self.refresh_queries(),
        )

        self.queries_tree = ttk.Treeview(self.left_frame, columns=(1, 2), show="headings", selectmode="browse")
        self.queries_tree.heading(1, text="Code")
        self.queries_tree.heading(2, text="Description")
        self.queries_tree.column(1, width=100, stretch=False)
        self.queries_tree.column(2, width=250, stretch=True)
        self.queries_filter()

        self.queries_tree_scrollbar_y = ttk.Scrollbar(
            self.left_frame, orient="vertical", command=self.queries_tree.yview
        )
        self.queries_tree["yscrollcommand"] = self.queries_tree_scrollbar_y.set

        # placement des éléments dans la frame
        self.queries_label_filter.grid(row=0, column=0, padx=2, pady=2, sticky="nswe")
        self.queries_entry_filter.grid(row=0, column=1, columnspan=3, padx=2, pady=2, sticky="nswe")
        self.queries_btn_folder.grid(row=0, column=4, columnspan=1, padx=2, pady=2, sticky="nswe")
        self.queries_btn_refresh.grid(row=0, column=5, columnspan=2, padx=2, pady=2, sticky="nswe")
        self.queries_tree.grid(row=1, column=0, columnspan=7, padx=2, pady=2, sticky="nswe")
        self.queries_tree_scrollbar_y.grid(row=1, column=6, sticky="nse")

        # paramètrage poids lignes et colonnes
        self.left_frame.rowconfigure(1, weight=1)
        self.left_frame.columnconfigure(1, weight=1)

    def setup_ui_right_frame(self):
        self.right_frame = ttk.Frame(self.paned_window, padding=0, borderwidth=2)
        self.right_frame.grid(row=0, column=1, padx=0, pady=0, sticky="nswe")
        self.right_frame.rowconfigure(0, weight=1)
        self.right_frame.columnconfigure(0, weight=1)

        self.setup_ui_right_panned()

        self.setup_ui_params()
        self.setup_ui_output_and_btn()

        self.right_panned.add(self.params_outer, weight=1)
        self.right_panned.add(self.output_and_btn_frame, weight=0)

    def setup_ui_right_panned(self):
        self.right_panned = ttk.PanedWindow(self.right_frame, orient="vertical")
        self.right_panned.grid(row=0, column=0, padx=0, pady=0, sticky="nswe")
        self.right_panned.rowconfigure(0, weight=1)
        self.right_panned.columnconfigure(0, weight=1)

    def setup_ui_params(self):
        self.params_outer = ttk.LabelFrame(self.right_panned, borderwidth=2)
        self.params_label = ttk.Label(self.params_outer, text="Saisie des paramètres", style=self.style_frame_label)
        self.params_outer.config(labelwidget=self.params_label)

        self.params_outer.rowconfigure(0, weight=1)
        self.params_outer.columnconfigure(0, weight=1)

        self.params_canvas = tk.Canvas(self.params_outer, highlightthickness=0)
        self.params_canvas.rowconfigure(0, weight=1)
        self.params_canvas.columnconfigure(0, weight=1)

        self.params_scrollbar = ttk.Scrollbar(self.params_outer, orient="vertical", command=self.params_canvas.yview)
        self.params_canvas["yscrollcommand"] = self.params_scrollbar.set

        self.params_inner = ttk.Frame(self.params_canvas)

        self.params_outer.grid(row=0, column=0, padx=0, pady=0, sticky="nswe")
        self.params_canvas.grid(row=0, column=0, padx=0, pady=0, sticky="nswe")
        self.params_scrollbar.grid(row=0, column=1, sticky="ns")
        self._windows = self.params_canvas.create_window((0, 0), window=self.params_inner, anchor="nw")

    def setup_ui_output_and_btn(self):
        self.output_and_btn_frame = ttk.Frame(self.right_panned, borderwidth=0)
        self.output_and_btn_frame.grid(row=1, column=0, padx=0, pady=0, sticky="nswe")

        self.setup_ui_output()
        self.setup_ui_btn()

        self.output_and_btn_frame.rowconfigure(0, weight=1)
        self.output_and_btn_frame.rowconfigure(1, weight=0)
        self.output_and_btn_frame.columnconfigure(0, weight=1)

    def setup_ui_output(self):
        self.setup_style()
        self.output_frame = ttk.LabelFrame(self.output_and_btn_frame, borderwidth=2)
        self.output_label = ttk.Label(
            self.output_frame, text="Messages / Fenêtre d'execution", style=self.style_frame_label
        )
        self.output_frame.config(labelwidget=self.output_label)

        self.output_frame.grid(row=0, column=0, padx=0, pady=0, sticky="nswe")

        self.output_textbox = tk.Text(
            self.output_frame,
            width=75,
            height=7,
            wrap="word",
            state="disabled",
            font=("TkDefaultFont", 10),
        )
        self.output_textbox_scrollbar = ttk.Scrollbar(
            self.output_frame, orient="vertical", command=self.output_textbox.yview
        )
        self.output_textbox["yscrollcommand"] = self.output_textbox_scrollbar.set
        self.output_textbox.see("1.0")

        self.output_textbox.grid(row=0, column=0, padx=2, pady=0, sticky="nswe")
        self.output_textbox_scrollbar.grid(row=0, column=1, sticky="nse")

        # paramètrage des poids des lignes et colonnes
        self.output_frame.rowconfigure(0, weight=1)
        self.output_frame.columnconfigure(0, weight=1)
        self.output_frame.columnconfigure(1, weight=0)

    def setup_ui_btn(self):
        self.btn_frame = ttk.Frame(self.output_and_btn_frame, borderwidth=2)
        self.btn_frame.grid(row=1, column=0, padx=0, pady=0, sticky="nswe")

        self.btn_execute = ttk.Button(self.btn_frame, text="Executer", state="disable", command=self.execute_query)
        self.btn_queries_folder = ttk.Button(
            self.btn_frame,
            text="Dossier",
            command=lambda: self.open_folder(SETTINGS.extract_folder),
        )
        self.btn_debug = ttk.Button(self.btn_frame, text="Debug", state="disable", command=self.debug_query)
        self.btn_quit = ttk.Button(self.btn_frame, text="Quitter", command=self.app_exit)

        self.btn_execute.grid(row=0, column=1, padx=2, pady=0, sticky="nswe")
        self.btn_queries_folder.grid(row=0, column=2, padx=2, pady=0, sticky="nswe")
        self.btn_debug.grid(row=0, column=3, padx=2, pady=0, sticky="nswe")
        self.btn_quit.grid(row=0, column=4, padx=0, pady=0, sticky="nswe")

        # paramètrage des poids des lignes et colonnes
        self.btn_frame.rowconfigure(0, weight=1)
        for column in range(self.btn_frame.grid_size()[0]):
            my_weight = 1 if column == 0 else 0
            self.btn_frame.columnconfigure(column, weight=my_weight)

    def position(self):
        self.update_idletasks()

        x = int(self.winfo_screenwidth() / 2 - self.winfo_width() / 2)
        y = int(self.winfo_screenheight() / 2 - self.winfo_height() / 1.8)

        self.geometry(f"+{x}+{y}")

    # ------------------------------------------------------------------------------------------
    # Gestion de l'interface pour la frame de saisie des paramètres
    # ------------------------------------------------------------------------------------------
    def ui_params_reset(self):
        for param_key in self.params_widgets:
            for widget_key in self.params_widgets[param_key]:
                if widget_key != "entry_var":
                    self.params_widgets[param_key][widget_key].destroy()

        self.params_widgets = {}

    def ui_params_update(self, params: dict[str, sql_query._Param] = None):
        self.ui_params_reset()

        params_number_not_hidden = 0
        if params is not None:
            for p in params:
                params_number_not_hidden += 1 if not params[p].is_hidden else 0

        if params_number_not_hidden == 0:
            self._ui_no_param_update()
        else:
            self._ui_with_params_update(params)

    def _ui_with_params_update(self, params: dict[str, sql_query._Param] = None):
        for i, key in enumerate(params):
            my_widgets = {}

            # ne pas afficher le paramètre si il doit être caché
            # et que l'utilisateur n'est pas un super utilisateur
            if params[key].is_hidden and not self.user.admin:
                continue

            my_widgets["label"] = ttk.Label(self.params_inner, text=params[key].description + " : ", justify=tk.LEFT)
            if params[key].is_hidden:
                my_widgets["label"]["text"] = "(*) " + params[key].description + " : "
                my_widgets["label"]["foreground"] = "red"

            my_widgets["entry_var"] = tk.StringVar(name=key, value=params[key].display_value)

            if params[key].ui_control == "check":
                my_widgets["entry"] = ttk.Checkbutton(self.params_inner, variable=my_widgets["entry_var"])
                my_widgets["entry"]["onvalue"] = "on"
                my_widgets["entry"]["offvalue"] = "off"
                my_widgets["entry_var"].trace_add("write", self.param_input_trace)

            elif params[key].ui_control == "list":
                my_widgets["entry"] = ttk.Combobox(self.params_inner, textvariable=my_widgets["entry_var"])
                my_widgets["entry"]["state"] = "readonly"
                my_widgets["entry"]["values"] = tuple(params[key].authorized_values.values())
                my_widgets["entry_var"].trace_add("write", self.param_input_trace)

            else:
                my_widgets["entry"] = ttk.Entry(self.params_inner, textvariable=my_widgets["entry_var"])
                my_widgets["entry"].bind("<FocusIn>", self.param_focus_event)
                my_widgets["entry"].bind("<FocusOut>", self.param_input_event)
                my_widgets["entry"].bind("<Return>", self.param_input_event)

            my_widgets["check"] = ttk.Label(
                self.params_inner,
                text="",
                width=3,
                relief="groove",
                justify=tk.LEFT,
                background="red",
            )

            my_widgets["label"].grid(row=i, column=0, padx=2, pady=2, sticky="nswe")
            my_widgets["entry"].grid(row=i, column=1, padx=2, pady=2, sticky="nswe")
            my_widgets["check"].grid(row=i, column=2, padx=2, pady=2, sticky="nswe")

            self.params_widgets[key] = my_widgets

        self.params_get_input()  # pour mise à jour aussi des widgets pour les checks

        # paramètrage des poids des lignes et colonnes
        for row in range(self.params_inner.grid_size()[1]):
            self.params_inner.rowconfigure(row, weight=0)

        self.params_inner.columnconfigure(0, weight=0)
        self.params_inner.columnconfigure(1, weight=1)
        self.params_inner.columnconfigure(2, weight=0)

    def _ui_no_param_update(self):
        my_widgets = {}

        my_widgets["label"] = ttk.Label(
            self.params_inner,
            text="Pas de paramètres à renseigner pour cette requête",
            font=("TkDefaultFont", 0, "bold"),
            wraplength=350,
            justify=tk.CENTER,
        )
        my_widgets["label"].grid(row=0, column=0, columnspan=2, sticky="ns")

        self.params_widgets["no_param"] = my_widgets

        self.params_inner.rowconfigure(0, weight=1)
        self.params_inner.columnconfigure(0, weight=1)

    # ------------------------------------------------------------------------------------------
    # Définition des évènements générer par les traitements
    # ------------------------------------------------------------------------------------------
    def setup_events_binds(self):
        self.queries_entry_filter.bind(
            "<KeyRelease>",
            lambda e: self.queries_filter(self.queries_entry_filter.get()),
        )

        self.queries_tree.bind("<<TreeviewSelect>>", self.tree_selection_change)

        self.params_canvas.bind("<Configure>", self.params_resize)
        self.params_canvas.bind(
            "<Enter>",
            lambda _: self.params_canvas.bind_all("<MouseWheel>", self.params_scrolling),
        )
        self.params_canvas.bind("<Leave>", lambda _: self.params_canvas.unbind_all("<MouseWheel>"))

        self.protocol("WM_DELETE_WINDOW", self.app_exit)  # arrêter le programme quand fermeture de la fenêtre

    # ------------------------------------------------------------------------------------------
    # Gestion thread pour execution de la requête
    # ------------------------------------------------------------------------------------------
    def start_execute_thread(self):  # démarrer par la méthode execute_query
        self.execute_thread_running = True

        self.logging_thread = Thread(target=self.start_logging_thread, daemon=True)  # thread pour recup msg process
        self.logging_thread.start()

        self.lock_ui()
        result = self.query.execute_cmd(file_output=True)
        rows_number, output_file = result if isinstance(result, tuple) else (0, "")
        self.execute_thread_running = False

        while self.logging_thread_running:  # attendre l'écriture de tous les messages
            time.sleep(0.100)

        self.unlock_ui()

        if rows_number > 0:
            answer = messagebox.askyesno("Fin execution", "Voulez vous ouvrir le fichier extrait ?")
            if answer:
                startfile(output_file)  # ouvrir le fichier
        else:
            messagebox.showinfo("Fin execution", "Aucune donnée extraite !")

    def lock_ui(self):
        self.menu_query.entryconfig("Executer", state="disable")

        self.btn_execute["state"] = "disable"
        self.queries_btn_folder["state"] = "disable"
        self.queries_entry_filter["state"] = "disable"
        self.queries_btn_refresh["state"] = "disable"
        self.queries_tree["selectmode"] = "none"

        for key in self.params_widgets:
            if (widget_entry := self.params_widgets[key].get("entry", None)) is not None:
                widget_entry["state"] = "disable"

    def unlock_ui(self):
        self.menu_query.entryconfig("Executer", state="normal")

        self.btn_execute["state"] = "enable"
        self.queries_btn_folder["state"] = "enable"
        self.queries_entry_filter["state"] = "enable"
        self.queries_btn_refresh["state"] = "enable"
        self.queries_tree["selectmode"] = "browse"

        for key in self.params_widgets:
            if (widget_entry := self.params_widgets[key].get("entry", None)) is not None:
                widget_entry["state"] = "enable" if not isinstance(widget_entry, ttk.Combobox) else "readonly"

    # ------------------------------------------------------------------------------------------
    # Gestion thread pour log pendant l'execution de la requête
    # ------------------------------------------------------------------------------------------
    def start_logging_thread(self):  # démarrer par la méthode start_execute_thread
        self.logging_thread_running = True
        update_speed = 1

        my_counter = 0
        while self.execute_thread_running or len(self.query.msg_list) > my_counter:
            time.sleep(update_speed)
            my_msg_list = self.query.msg_list

            if len(my_msg_list) > my_counter:
                msg_to_print = "\n" + "\n".join(my_msg_list[my_counter:])
                if my_counter == 0:
                    msg_to_print = msg_to_print[1:]
                self.output_msg(msg_to_print, "end")
                my_counter = len(my_msg_list)

        self.logging_thread_running = False

    # ------------------------------------------------------------------------------------------
    # Traitements
    # ------------------------------------------------------------------------------------------
    def refresh_queries(self):
        if not self.is_authorized:
            return

        self.ui_params_reset()
        self.query = sql_query.Query()
        self.output_msg("")
        self.queries_filter_text = ""

        try:
            self.queries = sql_query.get_queries(self.queries_folder)
            self.queries_filter()  # rénitialiser l'UI en simulant un filtre sur aucun élément
        except ValueError as err:
            self.queries = {}
            self.queries_filter(msg_filter=err)

    def execute_query(self):
        if self.query is None:
            messagebox.showwarning("Warning", "Aucune requête de sélectionnée !")
            return False

        self.output_msg("")
        if self.params_get_input():
            self.output_msg("")
            self.execute_thread = Thread(target=self.start_execute_thread, daemon=True)  # thread execution requete
            self.execute_thread.start()
        else:
            self.output_msg(
                "Impossible d'executer, tant que des paramètres ne sont pas valides :\n",
                "1.0",
                "1.0",
            )

    def params_get_input(self):
        for key in self.params_widgets:
            w_entry_var = self.params_widgets[key].get("entry_var", None)
            w_check = self.params_widgets[key].get("check", None)

            if w_entry_var is not None:
                self.query.params_obj[key].display_value = w_entry_var.get()
                try:
                    self.query.update_values(key)
                    w_check["background"] = "green"
                except ValueError as err:
                    w_check["background"] = "red"
                    msg = "    - " + self.query.params_obj[key].description + " : " + str(err) + "\n"
                    self.output_msg(msg, "end")

        return self.query.values_ok()

    def app_exit(self, event: Event = None):
        self.quit()

    def output_msg(self, txt_message: str, start_pos: str = "1.0", end_pos: str = "end"):
        try:  # erreur à l'initialisation quand le ctrl n'existe pas encore
            self.output_textbox["state"] = "normal"
            self.output_textbox.replace(start_pos, end_pos, str(txt_message))
            self.output_textbox.see(tk.END)
            self.output_textbox["state"] = "disabled"
        except AttributeError:
            pass

    def open_folder(self, folder: str):
        subprocess.Popen(f"explorer {folder}")

    # ------------------------------------------------------------------------------------------
    # Mise à jour de l'interface et des variables d'instances quand évènement
    # ------------------------------------------------------------------------------------------
    def queries_filter(self, text_filter: str = "", msg_filter: str = ""):
        for item in self.queries_tree.get_children():
            self.queries_tree.delete(item)

        self.ui_params_reset()
        self.query = None
        self.output_msg(msg_filter)

        self.queries_tree.tag_configure("hidden", foreground="gray")

        for item in self.queries:
            if (
                text_filter == ""
                or item.name.lower().find(text_filter.lower()) != -1
                or item.description.lower().find(text_filter.lower()) != -1
            ):
                color = "none" if item.description[0:3] != "(*)" else "hidden"
                self.queries_tree.insert(
                    "",
                    tk.END,
                    values=(item.name, item.description),
                    iid=id(item),
                    tags=color,
                )

    def tree_selection_change(self, _: Event):
        selected_iid = self.queries_tree.focus()
        selected_values = self.queries_tree.item(selected_iid, "values")

        self.output_msg("")

        if selected_values == "":
            self.menu_query.entryconfig("Executer", state="disable")
            self.menu_query.entryconfig("Debug...", state="disable")
            self.btn_execute["state"] = "disable"
            self.btn_debug["state"] = "disable"
            self.params_label["text"] = "Saisie des paramètres"
        else:
            self.menu_query.entryconfig("Executer", state="normal")
            self.menu_query.entryconfig("Debug...", state="normal")
            self.btn_execute["state"] = "enable"
            self.btn_debug["state"] = "enable"
            self.params_label["text"] = "Saisie des paramètres pour " + selected_values[0]
            for query in self.queries:
                if selected_iid == str(id(query)):
                    self.query = query
                    self.ui_params_update(self.query.params_obj)
                    break

        self.params_canvas.yview_moveto(0)
        self.params_resize()

    def param_focus_event(self, event: Event):
        widget = event.widget

        if widget.widgetName == "ttk::entry":
            widget: ttk.Entry
            widget.icursor("end")
            widget.select_range("0", "end")

    def param_input_event(self, event: Event):
        widget = event.widget

        key = ""
        for param_key in self.params_widgets:
            for widget_key in self.params_widgets[param_key]:
                if widget is self.params_widgets[param_key][widget_key]:
                    key = param_key
                    break
            if key != "":
                break

        self._param_input(key)

        if event.keysym == "Return":
            self.param_focus_event(event)

    def param_input_trace(self, name, *_):
        self._param_input(name)

    def _param_input(self, key: str):
        self.query.params_obj[key].display_value = self.params_widgets[key]["entry_var"].get()

        try:
            self.query.update_values(key)
            color = "green"

            # verif si affichage correcte, sinon MAJ (cas des saisies dates notamment)
            if self.params_widgets[key]["entry"].widgetName == "ttk::entry":
                correct_display = self.query.params_obj[key].display_value
                if self.params_widgets[key]["entry_var"].get() != correct_display:
                    self.params_widgets[key]["entry_var"].set(correct_display)

        except ValueError as e:
            color = "red"
            self.output_msg(e)

        self.params_widgets[key]["check"]["background"] = color

    def params_resize(self, _: Event = None):
        self.update_idletasks()

        size_width = max(self.params_canvas.winfo_width(), self.params_inner.winfo_reqwidth())
        size_height = max(self.params_canvas.winfo_height(), self.params_inner.winfo_reqheight())
        self.params_canvas.itemconfig(self._windows, width=size_width, height=size_height)

        self.params_canvas.configure(scrollregion=self.params_canvas.bbox("all"))

    def params_scrolling(self, event: Event):
        self.params_canvas.yview_scroll(int(-1 * event.delta / 120), "units")

    # ------------------------------------------------------------------------------------------
    # Sous-fenêtres
    # ------------------------------------------------------------------------------------------
    def debug_query(self):
        if self.query is not None:
            DebugWindow(self.query, self)

    def manage_users(self):
        if getattr(self, "user_window", None) is None or not self.user_window.winfo_exists():
            self.user_window = UsersWindow(self)
        else:
            self.user_window.focus_set()

    def manage_servers(self):
        ServersWindow(self)
        SETTINGS.server.reload()

    def manage_settings(self):
        SettingsWindow(self)
        SETTINGS.reload()

    def manage_password(self):
        PasswordWindow(self)

    def about_info(self):
        AboutWindow(self)


if __name__ == "__main__":
    my_app = App()
    my_app.mainloop()
