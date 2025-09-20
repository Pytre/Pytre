import tkinter as tk
from time import sleep
from threading import Thread
from multiprocessing import Queue, Event as proc_get_event
from multiprocessing.synchronize import Event as ProcEvent
from tkinter import Event as tkEvent, ttk, messagebox, font

from pathlib import Path
from datetime import datetime

if not __package__:
    import syspath_insert  # noqa: F401  # disable unused-import warning

import old_files
import sql_query
import settings
import users
import user_prefs
import servers
import utils
import logs_central
from about import APP_NAME, APP_VERSION, APP_STATUS

import ui.ui_utils as ui_utils
from ui.save_as import save_as
from ui.MsgDialog import MsgDialog
from ui.MsgOverlay import MsgOverlay
from ui.app_logs import LogsWindow
from ui.app_debug import DebugWindow
from ui.app_users import UsersWindow
from ui.app_servers import ServersWindow
from ui.app_settings import SettingsWindow
from ui.app_password import PasswordWindow
from ui.app_console import ConsoleWindow
from ui.app_about import AboutWindow
from ui.app_theme import ThemeColors, set_theme, theme_is_on


class App(tk.Toplevel):
    def __init__(self):
        super().__init__()
        self.focus_set()
        self.master.withdraw()

        self.user: users.CurrentUser = users.CurrentUser()
        self.prefs: user_prefs.UserPrefs = user_prefs.UserPrefs()
        self.app_settings: settings.Settings = settings.Settings()
        self.servers: servers.Servers = servers.Servers()
        self.server_id: str = ""
        self.central_logs: logs_central.CentralLogs = None

        self.queries_all: list[sql_query.Query] = []
        self.queries: list[sql_query.Query] = []
        self.query: sql_query.Query = sql_query.Query()
        self.params_widgets: dict[str, ttk.Widget] = {}
        self.output_file: Path = ""

        self.process_running: bool = False
        self.queue: Queue = Queue()
        self.can_stop: ProcEvent = proc_get_event()
        self.force_stop: ProcEvent = proc_get_event()
        self.query_worker: sql_query.QueryWorker = sql_query.QueryWorker(self.queue, self.force_stop, self.can_stop)

        self.date_format = sql_query.PRINT_DATE_FORMAT

        self.setup_style()
        self.setup_ui()
        self.setup_events_binds()

        self.check_min_version()  # quitte l'application si pas ok
        self.check_user_access()  # quitte l'application si pas d'accès

        self.console_start()

        self._queries_first_load: bool = True
        self.refresh_queries(reload_servers=True)

        self.extract_folder_cleaning()

    def check_user_access(self):
        if not self.user.is_authorized:
            messagebox.showerror(
                "Erreur",
                "Vous n'êtes pas dans liste des utilisateurs autorisées !"
                + "\nDonnées d'identification :"
                + f"\n- User : {self.user.username}",
                parent=self,
            )
            self.app_exit()

    def version_used_gte_mini(self, used: str, mini: str) -> bool:
        """ctrl si version utilisé supérieure ou égale à version mini"""
        used_nums = list(map(lambda i: int(i), used.split(".")))
        mini_nums = list(map(lambda i: int(i), mini.split(".")))

        if not len(used_nums) == len(mini_nums):
            return False

        if used_nums == mini_nums:
            return True
        for i in range(len(mini_nums)):
            if used_nums[i] > mini_nums[i]:
                return True
            elif used_nums[i] < mini_nums[i]:
                return False

        return False

    def check_min_version(self):
        queries_folder = Path(self.app_settings.queries_folder)
        if not queries_folder.is_dir():
            messagebox.showerror(
                "Répertoire inexistant",
                f"Répertoire des requêtes non trouvée :\n{queries_folder.resolve()}",
                parent=self,
            )
            self.app_exit()

        if not self.version_used_gte_mini(self.app_settings.settings_version, self.app_settings.min_version_settings):
            messagebox.showerror(
                "Version settings.db",
                "Le fichier settings.db utilisé n'est pas à jour."
                f"\n\n- Version utilisée : {self.app_settings.settings_version}"
                f"\n- Version mini : {self.app_settings.min_version_settings}"
                "\n\nMerci d'utiliser le fichier des settings à jour",
                parent=self,
            )
            self.app_exit()

        if not self.version_used_gte_mini(APP_VERSION, self.app_settings.min_version_pytre):
            messagebox.showerror(
                f"Version {APP_NAME}",
                f"Votre version de {APP_NAME} n'est pas à jour."
                f"\n\n- Version utilisée : {APP_VERSION}"
                f"\n- Version mini : {self.app_settings.min_version_pytre}"
                "\n\nMerci d'utiliser une version à jour",
                parent=self,
            )
            self.app_exit()

    def extract_folder_cleaning(self):
        extract_folder = self.prefs.extract_folder

        files = old_files.old_files_list(extract_folder)  # liste des fichiers à supprimer
        files_nb = len(files)
        if files_nb:
            files_size = round(sum([size.stat().st_size for size in files]) / 1024**2, 2)
            files_date = old_files.most_recent_files(files)

            answer = messagebox.askyesno(
                "Suppression des anciennes extractions",
                f"Dans le dossier des extractions il existe {files_nb} fichiers "
                + f"datant d'avant le {files_date.strftime('%d/%m/%Y')}. "
                + f"Ils vont être supprimés pour libérer {files_size} Mo d'espace disque.\n\n"
                + "Vous pouvez choisir de ne pas les supprimer mais ce message reviendra à chaque ouverture.\n\n"
                + "Si des fichiers doivent être conservés cliquer sur non et changer les de répertoire "
                + "ou déplacer les dans un sous-répertoire.",
                parent=self,
                icon="warning",
            )

            if answer:
                old_files.old_files_delete(files)
            else:
                self.open_folder(extract_folder)

    # ------------------------------------------------------------------------------------------
    # Définition des styles / thème
    # ------------------------------------------------------------------------------------------
    def setup_style(self):
        set_theme(self)
        default_font = font.nametofont("TkDefaultFont")
        self.font_family = default_font.actual("family")
        self.font_size = default_font.actual("size")

        self.s_header = "Header.TLabelframe.Label"
        ttk.Style(self).configure(self.s_header, font=(self.font_family, self.font_size, "bold"))

    # ------------------------------------------------------------------------------------------
    # Création de l'interface
    # ------------------------------------------------------------------------------------------
    def setup_ui(self):
        app_version = APP_VERSION if not APP_STATUS else f"{APP_VERSION} ({APP_STATUS})"
        self.title(f"{APP_NAME} - V.{app_version}")

        icon_file = self.app_settings.app_path / "res" / "app.png"
        icon_img = tk.PhotoImage(file=icon_file)
        self.iconphoto(True, icon_img)

        self.minsize(width=1020, height=740)
        self.resizable(True, True)

        self.setup_ui_menu()
        self.setup_ui_paned_window()
        self.setup_ui_left_frame()
        self.setup_ui_right_frame()

        self.paned_window.add(self.left_frame, weight=1)
        self.paned_window.add(self.right_frame, weight=2)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.setup_ui_position()

    def setup_ui_menu(self):
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        self.menu_query = tk.Menu(menubar, tearoff=False)
        self.menu_query.add_command(label="Executer", state="disabled", command=self.execute_query)
        self.menu_query.add_command(label="Dossier...", command=lambda: self.open_folder(self.prefs.extract_folder))
        self.menu_query.add_command(label="Journal...", command=lambda: self.open_logs(True))
        self.menu_query.add_command(label="Debug...", state="disabled", command=self.debug_query)
        self.menu_query.add_separator()
        self.menu_query.add_command(label="Recharger", command=lambda: self.refresh_queries())
        if self.user.admin:
            self.menu_query.add_command(label="Liste orphelines...", command=self.orphan_queries)
            self.menu_query.add_command(
                label="Paramètrage...", command=lambda: self.open_folder(self.app_settings.queries_folder)
            )
        self.menu_query.add_separator()
        self.menu_query.add_command(label="Quitter", command=self.app_exit)
        menubar.add_cascade(label="Requêtes", menu=self.menu_query)

        if self.user.admin:
            menu_admin = tk.Menu(menubar, tearoff=False)
            menu_admin.add_command(label="Utilisateurs...", command=self.manage_users)
            menu_admin.add_command(label="Serveurs...", command=self.manage_servers)
            menu_admin.add_command(label="Paramètres généraux...", command=self.manage_settings)
            menu_admin.add_separator()
            menu_admin.add_command(label="Paramètres, mot de passe...", command=self.manage_password)
            menubar.add_cascade(label="Administration", menu=menu_admin)

        menu_about = tk.Menu(menubar, tearoff=False)
        menu_about.add_command(label="Ouvrir la console...", command=self.console)
        menu_about.add_separator()
        menu_about.add_command(label=f"À propos de {APP_NAME}...", command=self.about_info)
        menubar.add_cascade(label="?", menu=menu_about)

        if theme_is_on():
            menus: list[tk.Menu] = [menubar, self.menu_query, menu_admin, menu_about]
            for menu in menus:
                menu.config(
                    font=font.Font(family=self.font_family, size=self.font_size),
                    bg=ThemeColors.bg_base,
                    fg=ThemeColors.text_primary,
                    activebackground=ThemeColors.accent_light,
                    activeforeground=ThemeColors.text_primary,
                    activeborderwidth=0,
                )

    def setup_ui_paned_window(self):
        self.paned_window = ttk.PanedWindow(self, orient="horizontal")
        self.paned_window.grid(row=0, column=0, padx=5, pady=5, sticky="nswe")

    def setup_ui_left_frame(self):
        self.left_frame = ttk.Frame(self.paned_window, padding=1, borderwidth=2)
        self.left_frame.grid(row=0, column=0, padx=0, pady=0, sticky="nswe")

        self.servers_label = ttk.Label(self.left_frame, text="Serveur :", justify=tk.LEFT)
        self.servers_cb = ttk.Combobox(self.left_frame, width=20, state="readonly")

        self.queries_filter_text = tk.StringVar()
        self.queries_label_filter = ttk.Label(self.left_frame, text="Filtre :", justify=tk.LEFT)
        self.queries_entry_filter = ttk.Entry(self.left_frame, textvariable=self.queries_filter_text, width=25)
        self.queries_btn_refresh = ttk.Button(
            self.left_frame, text="\U00002b6e", width=4, command=lambda: self.refresh_queries()
        )

        self.queries_tree = ttk.Treeview(self.left_frame, columns=(1, 2), show="headings", selectmode="browse")
        self.queries_tree.heading(1, text="Code")
        self.queries_tree.heading(2, text="Description")
        self.queries_tree.column(1, width=100, stretch=False)
        self.queries_tree.column(2, width=250, stretch=True)

        self.queries_tree_scrollbar_y = ttk.Scrollbar(
            self.left_frame, orient="vertical", command=self.queries_tree.yview
        )
        self.queries_tree["yscrollcommand"] = self.queries_tree_scrollbar_y.set

        # placement des éléments dans la frame
        self.servers_label.grid(row=0, column=0, padx=2, pady=2, sticky="nswe")
        self.servers_cb.grid(row=0, column=1, padx=2, pady=2, sticky="nswe")
        self.queries_label_filter.grid(row=0, column=2, padx=2, pady=2, sticky="nswe")
        self.queries_entry_filter.grid(row=0, column=3, columnspan=3, padx=2, pady=2, sticky="nswe")
        self.queries_btn_refresh.grid(row=0, column=6, columnspan=2, padx=2, pady=2, sticky="nswe")
        self.queries_tree.grid(row=1, column=0, columnspan=8, padx=2, pady=2, sticky="nswe")
        self.queries_tree_scrollbar_y.grid(row=1, column=7, sticky="nse")

        # paramètrage poids lignes et colonnes
        self.left_frame.rowconfigure(1, weight=1)
        self.left_frame.columnconfigure(1, weight=1)
        self.left_frame.columnconfigure(3, weight=5)

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
        self.params_label = ttk.Label(self.params_outer, text="Saisie des paramètres", style=self.s_header)
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
        self.output_frame = ttk.LabelFrame(self.output_and_btn_frame, borderwidth=2)
        self.output_label = ttk.Label(self.output_frame, text="Messages / Fenêtre d'execution", style=self.s_header)
        self.output_frame.config(labelwidget=self.output_label)

        self.output_frame.grid(row=0, column=0, padx=0, pady=0, sticky="nswe")

        self.output_textbox = tk.Text(
            self.output_frame,
            width=75,
            height=7,
            wrap="word",
            state="disabled",
            font=("TkFixedFont", self.font_size),
        )
        self.output_textbox_scrollbar = ttk.Scrollbar(
            self.output_frame,
            orient="vertical",
            command=self.output_textbox.yview,
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

        self.btn_log = ttk.Button(self.btn_frame, text="\U0001f56e", width=4, command=self.open_logs)
        self.btn_execute = ttk.Button(self.btn_frame, text="Executer", state="disable", command=self.execute_query)
        self.btn_queries_folder = ttk.Button(
            self.btn_frame, text="Dossier", command=lambda: self.open_folder(self.prefs.extract_folder)
        )
        self.btn_debug = ttk.Button(self.btn_frame, text="Debug", state="disable", command=self.debug_query)

        self.btn_log.grid(row=0, column=0, padx=2, pady=0, sticky="nswe")
        self.btn_execute.grid(row=0, column=2, padx=2, pady=0, sticky="nswe")
        self.btn_queries_folder.grid(row=0, column=3, padx=2, pady=0, sticky="nswe")
        self.btn_debug.grid(row=0, column=4, padx=2, pady=0, sticky="nswe")

        # paramètrage des poids des lignes et colonnes
        self.btn_frame.rowconfigure(0, weight=1)
        for column in range(self.btn_frame.grid_size()[0]):
            my_weight = 1 if column == 1 else 0
            self.btn_frame.columnconfigure(column, weight=my_weight)

    def setup_ui_position(self):
        if not utils.get_system() == "Windows":
            self.geometry("1100x750")

        ui_utils.ui_center(self)

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
                my_widgets["entry"].bind("<FocusOut>", lambda _: my_widgets["entry"].selection_clear())

            else:
                my_widgets["entry"] = ttk.Entry(self.params_inner, textvariable=my_widgets["entry_var"])
                my_widgets["entry"].bind("<FocusIn>", self.param_focus_event)
                my_widgets["entry"].bind("<FocusOut>", self.param_input_event)
                my_widgets["entry"].bind("<Return>", self.param_input_event)
                my_widgets["entry"].bind("<KP_Enter>", self.param_input_event)

            my_widgets["check"] = ttk.Label(
                self.params_inner, text="", width=3, relief="groove", justify=tk.LEFT, background="red"
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
        self.servers_cb.bind("<<ComboboxSelected>>", lambda _: self.queries_filter())
        self.servers_cb.bind("<FocusOut>", lambda _: self.servers_cb.selection_clear())
        self.queries_entry_filter.bind("<KeyRelease>", lambda _: self.queries_filter(self.queries_filter_text.get()))
        self.queries_tree.bind("<<TreeviewSelect>>", self.tree_selection_change)

        self.params_canvas.bind("<Configure>", self.params_resize)
        self.params_canvas.bind(
            "<Enter>", lambda _: self.params_canvas.bind_all("<MouseWheel>", self.params_scrolling)
        )
        self.params_canvas.bind("<Leave>", lambda _: self.params_canvas.unbind_all("<MouseWheel>"))

        self.bind("<Control-f>", lambda _: self.queries_filter_focus())
        self.protocol("WM_DELETE_WINDOW", self.app_exit)  # arrêter le programme quand fermeture de la fenêtre

    # ------------------------------------------------------------------------------------------
    # Execution de requête
    # ------------------------------------------------------------------------------------------
    def worker_check(self) -> bool:
        wait_counter: int = 0
        while self.query_worker.creating_worker:
            if wait_counter == 0:
                self.output_msg("En attente de la création d'un nouveau sous processus...")
            elif wait_counter == 10:
                self.output_msg(
                    "Echec ! Le sous processus ne semble pas disponible...\n" + "Lancement de la requête annulée !"
                )
                return False

            sleep(1)
            wait_counter += 1

        return True

    def exec_start(self):
        if not self.worker_check():
            return

        self.rows_number: int = 0
        self.output_file = ""
        self.can_stop.set()
        self.force_stop.clear()

        print("UI - Execution starting")
        self.process_running = True
        self.lock_ui(query_exec=True)

        serialized_query = self.query.serialize()
        self.query_worker.input_task(self.server_id, serialized_query)
        self.exec_check_queue()

    def exec_check_queue(self):
        done: bool = False
        try:
            while not self.queue.empty():
                msg_type, data = self.queue.get_nowait()
                if msg_type == "msg_print":
                    print(data)
                elif msg_type == "msg_output":
                    print(data)
                    # si output pas vide, rajout retour à la ligne
                    if self.output_textbox.get("1.0", "end-1c"):
                        data = "\n" + data
                    self.output_msg(data, "end")
                elif msg_type == "result":
                    self.rows_number, self.output_file = data
                elif msg_type == "error":
                    msg = f"Erreur non gérée :\n{data}"
                    print(msg)
                    self.output_msg(msg, "end")
                elif msg_type == "central_log" and self.instanciate_logs():
                    self.central_logs.trigger_sync(**data)
                elif msg_type == "done":
                    done = True
        except Exception as e:
            print(f"Error reading queue: {e}")
            done = True

        # si traitemnent fini ou erreur alors passer aux étapes de fin
        if done:
            self.exec_finish()
            return

        # si demande d'interruption et que l'execution n'est pas déjà finie
        if self.force_stop.is_set() and self.process_running:
            self.exec_force_stop()
            self.exec_finish()

        # auto relance toutes les x ms
        self.after(100, self.exec_check_queue)

    def exec_force_stop(self):
        self.query_worker.kill_and_restart()
        msg = datetime.now().strftime(self.date_format) + " - Requête interrompue"
        if not self.output_textbox.index("end-1c").split(".")[1] == "0":  # si on est pas en début de ligne
            msg = "\n" + msg
        self.output_msg(msg, "end")

    def exec_finish(self):
        self.unlock_ui(query_exec=True)
        self.process_running = False
        print("UI - Execution ending")

        # si arrêt forcé
        if self.force_stop.is_set():
            messagebox.showwarning("Fin execution", "Execution interrompue !", parent=self)
            return

        # si aucune ligne extraite
        if self.rows_number == 0:
            messagebox.showinfo("Fin execution", "Aucune donnée extraite !", parent=self)
            return

        # si des lignes ont été extraites proposer d'ouvrir ou enregistrer ailleurs le fichier
        answer = MsgDialog.ask(
            "Fin execution",
            "Le fichier extrait a été enregisté.\nVoulez-vous l'ouvrir ou enregistrer une copie ailleurs ?",
            buttons_txt=("Ouvrir", "Enregistrer", "Annuler"),
            parent=self,
        )

        # Ouverture directe du fichier
        if answer == "Ouvrir":
            utils.startfile(self.output_file)
            return

        # Enregistrement d'une copie
        if answer == "Enregistrer":
            file_copy = save_as(self, self.output_file)

            if not file_copy:
                return
            if not Path(file_copy).exists():
                messagebox.showerror("Fichier enregistrer sous", "Erreur copie fichier !", parent=self)
                return

            answer = MsgDialog.ask(
                "Fichier enregistrer sous",
                "La copie du fichier extrait a bien été enregistée.\nVoulez-vous l'ouvrir ?",
                buttons_txt=("Oui", "Non"),
                parent=self,
            )
            if answer == "Oui":
                utils.startfile(file_copy)

    # ------------------------------------------------------------------------------------------
    # Verrouillage / Déverrouillage de l'UI
    # ------------------------------------------------------------------------------------------
    def lock_ui(self, query_exec: bool = False):
        if query_exec:
            self.menu_query.entryconfig("Executer", label="Interrompre")
            self.btn_execute["text"] = "Interrompre"

        self.menu_query.entryconfig("Recharger", state="disable")
        self.menu_query.entryconfig("Liste orphelines...", state="disable")

        self.servers_cb["state"] = "disable"
        self.queries_entry_filter["state"] = "disable"
        self.queries_btn_refresh["state"] = "disable"
        self.queries_tree["selectmode"] = "none"

        for key in self.params_widgets:
            if (widget_entry := self.params_widgets[key].get("entry", None)) is not None:
                widget_entry["state"] = "disable"

    def unlock_ui(self, query_exec: bool = False):
        if query_exec:
            self.menu_query.entryconfig("Interrompre", label="Executer")
            self.btn_execute["text"] = "Executer"

        self.menu_query.entryconfig("Recharger", state="normal")
        self.menu_query.entryconfig("Liste orphelines...", state="normal")

        self.servers_cb["state"] = "readonly"
        self.queries_entry_filter["state"] = "enable"
        self.queries_btn_refresh["state"] = "enable"
        self.queries_tree["selectmode"] = "browse"

        for key in self.params_widgets:
            if (widget_entry := self.params_widgets[key].get("entry", None)) is not None:
                widget_entry["state"] = "enable" if not isinstance(widget_entry, ttk.Combobox) else "readonly"

    # ------------------------------------------------------------------------------------------
    # Traitements
    # ------------------------------------------------------------------------------------------
    def refresh_queries(self, reload_servers: bool = False):
        overlay = MsgOverlay.display(self, "Chargement des requêtes...", 1500)

        self.lock_ui()
        self.ui_params_reset()
        self.query = sql_query.Query()
        self.output_msg("")
        self.queries_filter_text.set("")

        def worker():
            queries_all, errors = [], []
            try:
                self.refresh_servers(reload_servers)
                queries_all, errors = sql_query.get_queries(self.app_settings.queries_folder)
            except Exception as err:
                queries_all, errors = [], [str(err)]
            finally:
                # retour dans le thread principal pour mettre à jour l'UI et finaliser
                self.after(0, lambda: refresh_end(queries_all, errors))

        def refresh_end(queries_all, errors):
            self.queries_all = queries_all
            self.queries_filter()
            self.tree_autosize()

            if self._queries_first_load and self.user.msg_login:
                self.output_msg(str(self.user.msg_login) + "\n", "1.0", "1.0")

            if errors:
                self.output_msg("\n".join(errors))

            overlay.hide(callback=self.unlock_ui)
            if not self._queries_first_load:
                messagebox.showinfo("Rechargement", "Ok requêtes rechargées", parent=self)
            else:
                self._queries_first_load = False

        Thread(target=worker, daemon=True).start()

    def refresh_servers(self, reload_servers: bool = False):
        grp_authorized = self.user.grp_authorized if not self.user.admin else None
        servers_dict: dict = self.servers.get_all_servers(reload_servers, grp_authorized)
        servers_name = [v.description or k for k, v in servers_dict.items()]
        servers_id = list(servers_dict.keys())

        self.servers_cb["values"] = tuple(servers_name)
        if servers_dict:
            if self.servers_cb.current() == -1:
                self.server_id = self.prefs.get(user_prefs.UserPrefsEnum.last_server)

            pos = servers_id.index(self.server_id) if self.server_id in servers_id else 0
            self.server_id = servers_id[pos]
            self.servers_cb.set(servers_name[pos])

        if len(self.servers.servers_dict) <= 1:
            # if one server or less hide servers selection
            self.servers_label.grid_remove()
            self.servers_cb.grid_remove()
            self.left_frame.columnconfigure(1, weight=0)
        elif not self.servers_cb.grid_remove():
            # display servers selection if hidden and more than one server
            self.servers_label.grid()
            self.servers_cb.grid()
            self.left_frame.columnconfigure(1, weight=1)

    def execute_query(self):
        if self.query is None:
            messagebox.showwarning("Warning", "Aucune requête de sélectionnée !", parent=self)
            return False

        # si une requête est en cours d'execution alors la stopper
        if self.query_worker.process and self.process_running:
            self.force_stop.set()
            return False

        self.output_msg("")
        if self.params_get_input():
            self.exec_start()
        else:
            self.output_msg("Impossible d'executer, tant que des paramètres ne sont pas valides :\n", "1.0", "1.0")

    def params_get_input(self):
        for key in self.params_widgets:
            w_entry_var = self.params_widgets[key].get("entry_var", None)
            w_check = self.params_widgets[key].get("check", None)

            if w_entry_var is not None:
                self.query.params_obj[key].display_value = w_entry_var.get()
                try:
                    self.query.update_values(key)
                    if self.query.params_obj[key].ctr_pattern_is_ok:
                        w_check["background"] = "green"
                    else:
                        w_check["background"] = "darkorange"
                        ctrl_pattern = self.query.params_obj[key].ctrl_pattern
                        msg = f"{w_entry_var.get()} ne correspond pas au format attendu : {ctrl_pattern}"
                        self.output_msg(msg)
                except ValueError as err:
                    w_check["background"] = "red"
                    msg = "    - " + self.query.params_obj[key].description + " : " + str(err) + "\n"
                    self.output_msg(msg, "end")

        return self.query.values_ok()

    def instanciate_logs(self) -> bool:
        if self.central_logs:
            return True
        elif self.app_settings.logs_are_on:
            LogsClass = logs_central.get_default_class()
            logs_folder = self.app_settings.logs_folder
            self.central_logs = LogsClass(logs_folder)

            return True

        return False

    def app_exit(self, event: tkEvent = None):
        if self.instanciate_logs():
            self.central_logs.stop_sync()

        self.prefs.set(user_prefs.UserPrefsEnum.last_server, self.server_id)

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
        if folder == self.prefs.extract_folder and self.output_file:
            utils.showfile(self.output_file)
        else:
            utils.showfile(folder)

    def orphan_queries(self):
        orphans = sql_query.orphan_queries(self.app_settings.queries_folder)
        if not orphans:
            self.output_msg("Aucune requête orpheline !")
        else:
            self.output_msg("Liste des requêtes orphelines :\n")
            for orphan in orphans:
                self.output_msg(f"{orphan.name}\n", "end")

    # ------------------------------------------------------------------------------------------
    # Mise à jour de l'interface et des variables d'instances quand évènement
    # ------------------------------------------------------------------------------------------
    def queries_filter_focus(self):
        self.queries_disable_filter = True
        self.queries_entry_filter.focus()

    def queries_filter(self, text_filter: str = "", msg_filter: str = ""):
        try:
            if self.queries_disable_filter and text_filter == self.queries_previous_filter:
                return
        except AttributeError:
            self.queries_disable_filter: bool
            self.queries_previous_filter: str

        self.queries_disable_filter = False
        self.queries_previous_filter = text_filter

        for item in self.queries_tree.get_children():
            self.queries_tree.delete(item)

        self.ui_params_reset()
        self.query = None
        self.output_msg(msg_filter)

        servers_id = list(self.servers.servers_dict.keys())
        cb_current = self.servers_cb.current()
        self.server_id = servers_id[cb_current] if cb_current > -1 else ""
        self.queries = sql_query.filter_queries(self.queries_all, self.server_id, self.user)

        self.queries_tree.tag_configure("hidden", foreground="gray")
        for item in self.queries:
            if (
                text_filter == ""
                or item.name.lower().find(text_filter.lower()) != -1
                or item.description.lower().find(text_filter.lower()) != -1
            ):
                color = "none" if item.description[0:3] != "(*)" else "hidden"
                self.queries_tree.insert("", tk.END, values=(item.name, item.description), iid=id(item), tags=color)

    def tree_autosize(self):
        cols_to_autosize = (0,)  # uniquement première colonne
        tkfont = font.nametofont("TkTextFont")
        for col in cols_to_autosize:
            max_width = tkfont.measure(self.queries_tree.heading(col)["text"] + "    ")
            for item in self.queries_tree.get_children(""):
                item_width = tkfont.measure(self.queries_tree.set(item, col) + "    ")
                max_width = max(max_width, item_width)
            self.queries_tree.column(col, width=max_width)

    def tree_selection_change(self, _: tkEvent):
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

    def param_focus_event(self, event: tkEvent):
        widget = event.widget

        if widget.widgetName == "ttk::entry":
            widget: ttk.Entry
            widget.icursor("end")
            widget.select_range("0", "end")

    def param_input_event(self, event: tkEvent):
        widget = event.widget

        if event.type == tk.EventType.FocusOut:
            widget.selection_clear()

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

            # si valeur ne correspond pas au pattern de ctrl alors couleur orange
            if not self.query.params_obj[key].ctr_pattern_is_ok:
                color = "darkorange"
                ctrl_pattern = self.query.params_obj[key].ctrl_pattern
                msg = f"{correct_display} ne correspond pas au format attendu : {ctrl_pattern}"
                self.output_msg(msg)

        except ValueError as e:
            color = "red"
            self.output_msg(e)

        self.params_widgets[key]["check"]["background"] = color

    def params_resize(self, _: tkEvent = None):
        self.update_idletasks()

        size_width = max(self.params_canvas.winfo_width(), self.params_inner.winfo_reqwidth())
        size_height = max(self.params_canvas.winfo_height(), self.params_inner.winfo_reqheight())
        self.params_canvas.itemconfig(self._windows, width=size_width, height=size_height)

        self.params_canvas.configure(scrollregion=self.params_canvas.bbox("all"))

    def params_scrolling(self, event: tkEvent):
        self.params_canvas.yview_scroll(int(-1 * event.delta / 120), "units")

    # ------------------------------------------------------------------------------------------
    # Sous-fenêtres
    # ------------------------------------------------------------------------------------------
    def open_logs(self, all: bool = False):
        if not all and self.query is not None:
            LogsWindow(self, self.query.name)
        else:
            LogsWindow(self)

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

    def manage_settings(self):
        SettingsWindow(self, self.app_settings.reload)

    def manage_password(self):
        PasswordWindow(self)

    def console_start(self):
        self.console: tk.Toplevel = ConsoleWindow(self, hide=True)

    def console(self):
        if not self.console.winfo_exists():
            self.console_start
        self.console.deiconify()

    def about_info(self):
        AboutWindow(self)


if __name__ == "__main__":
    my_app = App()
    my_app.mainloop()
