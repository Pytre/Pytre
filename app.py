import time
import typing
import tkinter as tk
from tkinter import Event, ttk, messagebox
from threading import Thread
from pathlib import Path

import settings, utils, sql_user, sql_query

APP_PATH = Path(utils.get_app_path())  # dossier ou les fichiers de l'executable sont extraits
PYTRE_VERSION = "0.803"


class App(tk.Tk):
    def __init__(self):
        super().__init__()

        self.user = sql_user.User()

        self.queries: typing.List[sql_query.Query] = sql_query.get_queries(APP_PATH / settings.QUERY_FOLDER)
        self.current_query: sql_query.Query = self.queries[0]
        self.params_widgets = {}

        self.setup_ui()
        self.setup_events_binds()

        self.manage_user_authorization()

    def manage_user_authorization(self):
        if not self.user.is_authorized:
            messagebox.showerror(
                "Erreur",
                "Désolé, vous n'êtes pas dans liste des utilisateurs autorisées !",
            )
            self.destroy()
        else:
            self.output_message(str(self.user.msg_login))

    # ------------------------------------------------------------------------------------------
    # Création de l'interface
    # ------------------------------------------------------------------------------------------
    def setup_ui(self):
        self.title(f"Pytre X3 - Python Requeteur pour X3 - V.{PYTRE_VERSION}")
        icon_file = APP_PATH / "res" / "app.ico"
        self.iconbitmap(icon_file)

        self.minsize(width=800, height=650)
        self.resizable(True, True)

        self.setup_ui_paned_window()
        self.setup_ui_left_frame()
        self.setup_ui_right_frame()

        self.paned_window.add(self.left_frame, weight=0)
        self.paned_window.add(self.right_frame, weight=1)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.center_window()

    def setup_ui_paned_window(self):
        self.paned_window = ttk.PanedWindow(self, orient="horizontal")
        self.paned_window.grid(row=0, column=0, padx=5, pady=5, sticky="nswe")

    def setup_ui_left_frame(self):
        self.left_frame = ttk.Frame(self.paned_window, padding=1, borderwidth=2)
        self.left_frame.grid(row=0, column=0, padx=0, pady=0, sticky="nswe")

        self.queries_filter_text = tk.StringVar()

        self.queries_label_filter = ttk.Label(self.left_frame, text="Filtre :", justify=tk.LEFT)
        self.queries_entry_filter = ttk.Entry(self.left_frame, textvariable=self.queries_filter_text, width=30)
        self.queries_btn_filter = ttk.Button(
            self.left_frame,
            text="Appliquer",
            command=lambda: self.queries_list_filter(self.queries_entry_filter.get()),
        )

        self.queries_tree = ttk.Treeview(self.left_frame, columns=(1, 2), show="headings", selectmode="browse")
        self.queries_tree.heading(1, text="Code")
        self.queries_tree.heading(2, text="Description")
        self.queries_tree.column(1, width=20)
        self.queries_tree.column(2, width=100)
        self.queries_list_filter()

        self.queries_tree_scrollbar_y = ttk.Scrollbar(
            self.left_frame, orient="vertical", command=self.queries_tree.yview
        )
        self.queries_tree["yscrollcommand"] = self.queries_tree_scrollbar_y.set

        # placement des éléments dans la frame
        self.queries_label_filter.grid(row=0, column=0, padx=2, pady=2, sticky="nswe")
        self.queries_entry_filter.grid(row=0, column=1, columnspan=3, padx=2, pady=2, sticky="nswe")
        self.queries_btn_filter.grid(row=0, column=4, columnspan=2, padx=2, pady=2, sticky="nswe")
        self.queries_tree.grid(row=1, column=0, columnspan=6, padx=2, pady=2, sticky="nswe")
        self.queries_tree_scrollbar_y.grid(row=1, column=5, sticky="nse")

        # paramètrage des poids des lignes et colonnes
        self.left_frame.rowconfigure(0, weight=0)
        self.left_frame.rowconfigure(1, weight=1)
        for column in range(self.left_frame.grid_size()[0]):
            if column == 0 or column >= self.left_frame.grid_size()[0] - 2:
                self.left_frame.columnconfigure(column, weight=0)
            else:
                self.left_frame.columnconfigure(column, weight=1)

    def setup_ui_right_frame(self):
        self.right_frame = ttk.Frame(self.paned_window, padding=0, borderwidth=2)
        self.right_frame.grid(row=0, column=1, padx=0, pady=0, sticky="nswe")

        self.setup_ui_right_panned()
        self.setup_ui_parameters_frame()
        self.setup_ui_output_and_btn_frame()

        self.right_frame.rowconfigure(0, weight=1)
        self.right_frame.columnconfigure(0, weight=1)

        self.right_panned.add(self.parameters_frame, weight=1)
        self.right_panned.add(self.output_and_btn_frame, weight=0)

    def setup_ui_right_panned(self):
        self.right_panned = ttk.PanedWindow(self.right_frame, orient="vertical")
        self.right_panned.grid(row=0, column=0, padx=0, pady=0, sticky="nswe")

    def setup_ui_parameters_frame(self):
        style = ttk.Style()
        self.style_label_txt_name = "Bold.TLabelFrame.Label"
        style.configure(self.style_label_txt_name, font=("TkDefaultFont", 10, "bold"))  # style texte pour label frame

        self.parameters_frame_label = ttk.Label(text="Saisie des paramètres", style=self.style_label_txt_name)
        self.parameters_frame = ttk.LabelFrame(
            self.right_panned,
            labelwidget=self.parameters_frame_label,
            padding=0,
            borderwidth=2,
        )

        self.parameters_frame.grid(row=0, column=0, padx=0, pady=0, sticky="nswe")

    def setup_ui_output_and_btn_frame(self):
        self.output_and_btn_frame = ttk.Frame(self.right_panned, padding=1, borderwidth=2)
        self.output_and_btn_frame.grid(row=1, column=0, padx=0, pady=0, sticky="nswe")

        self.setup_ui_output_frame()
        self.setup_ui_btn_frame()

        self.output_and_btn_frame.rowconfigure(0, weight=1)
        self.output_and_btn_frame.rowconfigure(1, weight=0)
        self.output_and_btn_frame.columnconfigure(0, weight=1)

    def setup_ui_output_frame(self):
        self.output_frame_label = ttk.Label(text="Messages / Fenêtre d'execution", style=self.style_label_txt_name)
        self.output_frame = ttk.LabelFrame(
            self.output_and_btn_frame,
            labelwidget=self.output_frame_label,
            padding=1,
            borderwidth=2,
        )
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

        self.output_textbox.grid(row=0, column=0, padx=0, pady=0, sticky="nswe")
        self.output_textbox_scrollbar.grid(row=0, column=1, sticky="nse")

        # paramètrage des poids des lignes et colonnes
        self.output_frame.rowconfigure(0, weight=1)
        self.output_frame.columnconfigure(0, weight=1)
        self.output_frame.columnconfigure(1, weight=0)

    def setup_ui_btn_frame(self):
        self.btn_frame = ttk.Frame(self.output_and_btn_frame, padding=1, borderwidth=2)
        self.btn_frame.grid(row=1, column=0, padx=0, pady=0, sticky="nswe")

        self.parameters_btn_execute = ttk.Button(
            self.btn_frame, text="Executer", state="disable", command=self.execute_query
        )
        self.parameters_btn_quit = ttk.Button(self.btn_frame, text="Quitter", command=self.app_exit)

        self.parameters_btn_execute.grid(row=0, column=1, padx=2, pady=0, sticky="nswe")
        self.parameters_btn_quit.grid(row=0, column=2, padx=0, pady=0, sticky="nswe")

        # paramètrage des poids des lignes et colonnes
        self.btn_frame.rowconfigure(0, weight=1)
        for column in range(self.btn_frame.grid_size()[0]):
            if column == 0:
                self.btn_frame.columnconfigure(column, weight=1)
            else:
                self.btn_frame.columnconfigure(column, weight=0)

    def center_window(self):
        self.update_idletasks()

        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        app_size = tuple(int(_) for _ in self.geometry().split("+")[0].split("x"))

        x = screen_width / 2 - app_size[0] / 2
        y = screen_height / 2 - app_size[1] / 2

        self.geometry("+%d+%d" % (x, y))

    # ------------------------------------------------------------------------------------------
    # Gestion de l'interface pour la frame de saisie des paramètres
    # ------------------------------------------------------------------------------------------
    def parameters_frame_reset(self):
        for param_key in self.params_widgets:
            for widget_key in self.params_widgets[param_key]:
                self.params_widgets[param_key][widget_key].destroy()

        self.params_widgets = {}

    def parameters_frame_update(self, params: typing.Dict[str, sql_query._Param] = None):
        self.parameters_frame_reset()
        if not params is None and not params == {}:
            for i, key in enumerate(params):
                my_widgets = {}

                my_widgets["label"] = ttk.Label(
                    self.parameters_frame,
                    text=params[key].description + " : ",
                    justify=tk.LEFT,
                )
                my_widgets["entry"] = ttk.Entry(self.parameters_frame)
                my_widgets["entry"].insert("end", params[key].display_value)
                my_widgets["check"] = ttk.Label(
                    self.parameters_frame,
                    text="",
                    width=3,
                    relief="groove",
                    justify=tk.LEFT,
                    background="red",
                )

                my_widgets["label"].grid(row=i, column=0, padx=2, pady=2, sticky="nswe")
                my_widgets["entry"].grid(row=i, column=1, padx=2, pady=2, sticky="nswe")
                my_widgets["check"].grid(row=i, column=2, padx=2, pady=2, sticky="nswe")

                my_widgets["entry"].bind("<FocusOut>", self.parameter_input)

                self.params_widgets[key] = my_widgets

            self.parameters_retrieve_user_input()  # pour mise à jour aussi des widgets pour les checks

            # paramètrage des poids des lignes et colonnes
            for row in range(self.parameters_frame.grid_size()[1]):
                self.parameters_frame.rowconfigure(row, weight=0)
            self.parameters_frame.columnconfigure(0, weight=0)
            self.parameters_frame.columnconfigure(1, weight=1)
            self.parameters_frame.columnconfigure(2, weight=0)
        else:
            my_widgets = {}
            my_widgets["label"] = ttk.Label(
                self.parameters_frame,
                text="Pas de paramètres à renseigner pour cette requête",
                font=("TkDefaultFont", 0, "bold"),
                wraplength=350,
                justify=tk.CENTER,
            )
            my_widgets["label"].grid(row=0, column=0, columnspan=2, sticky="ns")

            self.params_widgets["no_param"] = my_widgets

            self.parameters_frame.rowconfigure(0, weight=1)
            self.parameters_frame.columnconfigure(0, weight=1)

    # ------------------------------------------------------------------------------------------
    # Définition des évènements générer par les traitements
    # ------------------------------------------------------------------------------------------
    def setup_events_binds(self):
        self.queries_entry_filter.bind(
            "<Return>",
            lambda e: self.queries_list_filter(self.queries_entry_filter.get()),
        )
        self.queries_tree.bind("<<TreeviewSelect>>", self.tree_selection_change)
        self.protocol("WM_DELETE_WINDOW", self.app_exit)  # arrêter le programme quand fermeture de la fenêtre

    # ------------------------------------------------------------------------------------------
    # Gestion thread pour execution de la requête
    # ------------------------------------------------------------------------------------------
    def start_execute_thread(self):  # démarrer par la méthode execute_query
        self.execute_thread_running = True

        self.logging_thread = Thread(target=self.start_logging_thread, daemon=True)  # thread pour recup msg process
        self.logging_thread.start()

        self.lock_ui()
        self.current_query.execute_cmd()
        self.execute_thread_running = False

        while self.logging_thread_running:  # attendre l'écriture de tous les messages
            time.sleep(0.100)

        self.unlock_ui()

    def lock_ui(self):
        self.parameters_btn_execute["state"] = "disable"
        self.queries_btn_filter["state"] = "disable"
        self.queries_entry_filter["state"] = "disable"
        self.queries_tree["selectmode"] = "none"
        for key in self.params_widgets:
            if not (widget_entry := self.params_widgets[key].get("entry", None)) is None:
                widget_entry["state"] = "disable"

    def unlock_ui(self):
        self.parameters_btn_execute["state"] = "enable"
        self.queries_btn_filter["state"] = "enable"
        self.queries_entry_filter["state"] = "enable"
        self.queries_tree["selectmode"] = "browse"
        for key in self.params_widgets:
            if not (widget_entry := self.params_widgets[key].get("entry", None)) is None:
                widget_entry["state"] = "enable"

    # ------------------------------------------------------------------------------------------
    # Gestion thread pour log pendant l'execution de la requête
    # ------------------------------------------------------------------------------------------
    def start_logging_thread(self):  # démarrer par la méthode start_execute_thread
        self.logging_thread_running = True
        update_speed = 1

        my_counter = 0
        while self.execute_thread_running or len(self.current_query.msg_list) > my_counter:
            time.sleep(update_speed)
            my_msg_list = self.current_query.msg_list

            if len(my_msg_list) > my_counter:
                msg_to_print = "\n" + "\n".join(my_msg_list[my_counter:])
                if my_counter == 0:
                    msg_to_print = msg_to_print[1:]
                self.output_message(msg_to_print, "end")
                my_counter = len(my_msg_list)

        self.logging_thread_running = False

    # ------------------------------------------------------------------------------------------
    # Traitements
    # ------------------------------------------------------------------------------------------
    def execute_query(self):
        if self.current_query is None:
            messagebox.showwarning("Warning", "Aucune requête de sélectionnée !")
            return False

        self.output_message("")
        if self.parameters_retrieve_user_input():
            self.output_message("")
            self.execute_thread = Thread(target=self.start_execute_thread, daemon=True)  # thread execution requete
            self.execute_thread.start()
        else:
            self.output_message("Impossible d'executer, tant que des paramètres ne sont pas valides :\n", "1.0", "1.0")

    def parameters_retrieve_user_input(self):
        for key in self.params_widgets:
            widget_entry = self.params_widgets[key].get("entry", None)
            widget_check = self.params_widgets[key].get("check", None)

            if not widget_entry is None:
                self.current_query.params[key].display_value = widget_entry.get()
                try:
                    self.current_query.update_values(key)
                    widget_check["background"] = "green"
                except ValueError as err:
                    widget_check["background"] = "red"
                    msg = "    - " + self.current_query.params[key].description + " : " + str(err) + "\n"
                    self.output_message(msg, "end")

        return self.current_query.values_ok()

    def app_exit(self, event: Event=None):
        self.quit()

    # ------------------------------------------------------------------------------------------
    # Mise à jour de l'interface et des variables d'instances quand évènement
    # ------------------------------------------------------------------------------------------
    def queries_list_filter(self, text_filter: str = ""):
        for item in self.queries_tree.get_children():
            self.queries_tree.delete(item)

        self.parameters_frame_reset()
        self.current_query = None
        self.output_message("")

        for item in self.queries:
            if (
                text_filter == ""
                or not item.name.lower().find(text_filter.lower()) == -1
                or not item.description.lower().find(text_filter.lower()) == -1
            ):
                self.queries_tree.insert("", tk.END, values=(item.name, item.description), iid=id(item))

    def tree_selection_change(self, event: Event):
        selected_iid = self.queries_tree.focus()
        selected_values = self.queries_tree.item(selected_iid, "values")

        self.output_message("")

        if selected_values == "":
            self.parameters_btn_execute["state"] = "disable"
            self.parameters_frame_label["text"] = "Saisie des paramètres"
        else:
            self.parameters_btn_execute["state"] = "enable"
            self.parameters_frame_label["text"] = "Saisie des paramètres " + selected_values[0]
            for query in self.queries:
                if selected_iid == str(id(query)):
                    self.current_query = query
                    self.parameters_frame_update(self.current_query.params)
                    break

    def parameter_input(self, event: Event):
        widget = event.widget
        key = ""

        for param_key in self.params_widgets:
            for widget_key in self.params_widgets[param_key]:
                if widget is self.params_widgets[param_key][widget_key]:
                    key = param_key
                    break
            if not key == "":
                break

        self.current_query.params[key].display_value = widget.get()
        try:
            color = "green" if self.current_query.update_values(key) else "red"
        except ValueError as e:
            color = "red"
            self.output_message(e)

        self.params_widgets[key]["check"]["background"] = color

    def output_message(self, txt_message: str, start_pos: str = "1.0", end_pos: str = "end"):
        try:  # erreur à l'initialisation quand le ctrl n'existe pas encore
            self.output_textbox["state"] = "normal"
            self.output_textbox.replace(start_pos, end_pos, str(txt_message))
            self.output_textbox.see(tk.END)
            self.output_textbox["state"] = "disabled"
        except AttributeError:
            pass


if __name__ == "__main__":
    my_app = App()
    my_app.mainloop()
