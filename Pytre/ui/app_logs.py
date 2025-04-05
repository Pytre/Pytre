import tkinter as tk
import json
import subprocess
from tkinter import ttk, Event, messagebox
from datetime import datetime
from pathlib import Path
from os import startfile

if not __package__:
    import syspath_insert  # noqa: F401  # disable unused-import warning

import logs_user
from ui.save_as import save_as
from about import APP_NAME

DATE_FORMAT = "%d/%m/%Y"
TIME_FORMAT = "%H:%M:%S"


class LogsWindow(tk.Toplevel):
    def __init__(self, parent=None, query_name=""):
        super().__init__()
        self.parent = parent
        self.focus_set() if self.parent else self.master.withdraw()

        self.user_db: logs_user.UserDb = logs_user.UserDb()

        self.query_name: str = query_name
        self.saved_query_name: str = ""  # pour pouvoir refiltrer après tout montrer
        self.stats_mode: bool = False
        self.sort_info: dict[str, str | bool] = {"col": None, "reverse": None}

        self._setup_ui()
        self._events_binds()

        self.tree_refresh()
        self.tree_select_pos(0)
        self.tree.focus_set()

    # ------------------------------------------------------------------------------------------
    # Création de l'interface
    # ------------------------------------------------------------------------------------------
    def _setup_ui(self):
        self.title(f"{APP_NAME} - Extractions")
        if self.parent:
            self.geometry(f"640x750+{self.parent.winfo_x() + 130}+{self.parent.winfo_y() - 20}")
        else:
            self.geometry("640x750+100+75")

        self.resizable(True, True)

        self.top_frame = ttk.PanedWindow(self, orient=tk.VERTICAL)
        self.ctrl_frame = ttk.Frame(self, padding=1, borderwidth=2)

        self.top_frame.grid(row=0, column=0, padx=0, pady=0, sticky="nswe")
        self.ctrl_frame.grid(row=1, column=0, padx=0, pady=0, sticky="we")

        self._setup_ui_menu()
        self._setup_ui_top()
        self._setup_ui_ctrl()

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

    def _setup_ui_menu(self):
        self.menubar = tk.Menu(self, tearoff=False)

        self.config(menu=self.menubar)

        self.menu_extract = tk.Menu(self.menubar, tearoff=False)
        self.menu_extract.add_command(label="Ouvrir...", command=self.show_file)
        self.menu_extract.add_command(label="Enregistrer...", command=self.save_as)
        self.menu_extract.add_command(label="Dossier...", command=lambda: self.show_file(True))
        self.menu_extract.add_command(label="Recharger", command=self.tree_refresh)
        self.menu_extract.add_separator()
        self.menu_extract.add_command(label="Afficher tout", command=self.show_all_queries)
        self.menu_extract.add_command(label="Vue stats", command=self.show_all_stats)
        self.menu_extract.add_separator()
        self.menu_extract.add_command(label="Fermer", command=self.app_exit)
        self.menubar.add_cascade(label="Extractions", menu=self.menu_extract)

    def _setup_ui_ctrl(self):
        self.btn_stats = ttk.Button(self.ctrl_frame, text="Stats", command=self.show_query_stats)
        self.btn_open = ttk.Button(self.ctrl_frame, text="Ouvrir", command=self.show_file)
        self.btn_save_as = ttk.Button(self.ctrl_frame, text="Enregistrer", command=self.save_as)
        self.btn_folder = ttk.Button(self.ctrl_frame, text="Dossier", command=lambda: self.show_file(True))

        self.btn_stats.grid(row=0, column=0, padx=2, pady=2, sticky="nswe")
        self.btn_open.grid(row=0, column=2, padx=2, pady=2, sticky="nswe")
        self.btn_save_as.grid(row=0, column=3, padx=2, pady=2, sticky="nswe")
        self.btn_folder.grid(row=0, column=4, padx=2, pady=2, sticky="nswe")

        self.ctrl_frame.grid_rowconfigure(0, weight=1)
        self.ctrl_frame.grid_columnconfigure(1, weight=1)

    def _setup_ui_top(self):
        self.tree_frame = ttk.Frame(self.top_frame, padding=1, borderwidth=2)
        self.params_frame = ttk.Frame(self.top_frame, padding=1, borderwidth=2)

        self._setup_ui_tree()
        self._setup_ui_params()

        self.top_frame.add(self.tree_frame, weight=2)
        self.top_frame.add(self.params_frame, weight=1)

        self.top_frame.grid_rowconfigure(0, weight=1)
        self.top_frame.grid_columnconfigure(0, weight=1)

    def _tree_cols_grp(self) -> dict:
        common = {
            "num": {"attr": "", "text": "Num", "width": 45, "anchor": "e", "stretch": False},
            "server": {"attr": "server", "text": "Serveur", "width": 100, "stretch": False},
            "query": {"attr": "query", "text": "Requête", "width": 125, "stretch": False},
        }
        queries = {
            "date": {"attr": "start", "text": "Date", "width": 75, "anchor": "e", "stretch": False},
            "time": {"attr": "start", "text": "Heure", "width": 75, "anchor": "e", "stretch": False},
            "duration": {"attr": "duration", "text": "Durée", "width": 75, "anchor": "e", "stretch": False},
            "nb_rows": {"attr": "nb_rows", "text": "Lignes", "width": 75, "anchor": "e", "stretch": False},
            "file": {"attr": "file", "text": "", "width": 40, "stretch": True},
        }
        queries_hidden = {
            "fullpath": {"attr": "file", "text": "", "width": 40, "stretch": False},
            "parameters": {"attr": "parameters", "text": "", "width": 40, "stretch": False},
        }
        stats = {
            "nb_run": {"attr": "nb_run", "text": "Nb", "width": 45, "anchor": "e", "stretch": False},
            "min_run": {"attr": "min_run", "text": "Min", "width": 75, "anchor": "e", "stretch": False},
            "max_run": {"attr": "max_run", "text": "Max", "width": 75, "anchor": "e", "stretch": False},
            "last_date": {"attr": "last_run", "text": "Date", "width": 75, "anchor": "e", "stretch": False},
            "last_time": {"attr": "last_run", "text": "Heure", "width": 70, "anchor": "e", "stretch": True},
        }

        cols_grp = {
            "common": common,
            "queries": queries,
            "queries_hidden": queries_hidden,
            "stats": stats,
        }

        return cols_grp

    def _tree_cols_all(self) -> dict:
        cols_grp: dict[str, dict] = self._tree_cols_grp()
        cols: dict = {}
        for _, c in cols_grp.items():
            cols.update(c)

        return cols

    def _setup_ui_tree(self):
        cols: dict = self._tree_cols_all()

        self.tree = ttk.Treeview(
            self.tree_frame, height=20, columns=list(cols.keys()), show="headings", selectmode="browse"
        )
        for col, attr in cols.items():
            self.tree.heading(col, text=attr["text"], command=lambda c=col: self.tree_header_click(c))
            self.tree.column(col, width=attr["width"], stretch=attr["stretch"])
            if val := attr.get("anchor", ""):
                self.tree.column(col, anchor=val)

        xbar = ttk.Scrollbar(self.tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(xscroll=xbar.set)
        ybar = ttk.Scrollbar(self.tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=ybar.set)

        self.tree.grid(row=0, column=0, columnspan=1, padx=2, pady=2, sticky="nswe")
        xbar.grid(row=1, column=0, sticky="we")
        ybar.grid(row=0, column=1, sticky="ns")

        self.tree_frame.grid_rowconfigure(0, weight=1)
        self.tree_frame.grid_columnconfigure(0, weight=1)

    def _setup_ui_params(self):
        self.textbox = tk.Text(self.params_frame, height=14, width=400, wrap="none", state=tk.DISABLED)
        x_scrollbar = ttk.Scrollbar(self.params_frame, orient="horizontal", command=self.textbox.xview)
        y_scrollbar = ttk.Scrollbar(self.params_frame, orient="vertical", command=self.textbox.yview)

        self.textbox["xscrollcommand"] = x_scrollbar.set
        self.textbox["yscrollcommand"] = y_scrollbar.set

        self.textbox.grid(row=0, column=0, sticky="nswe")
        x_scrollbar.grid(row=1, column=0, sticky="we")
        y_scrollbar.grid(row=0, column=1, sticky="ns")

        self.params_frame.grid_columnconfigure(0, weight=1)
        self.params_frame.grid_rowconfigure(0, weight=1)

    # ------------------------------------------------------------------------------------------
    # Définition des évènements générer par les traitements
    # ------------------------------------------------------------------------------------------
    def _events_binds(self):
        self.tree.bind("<<TreeviewSelect>>", lambda _: self.show_query_params())
        self.tree.bind("<Double-Button-1>", lambda _: self.show_file(True))
        self.tree.bind("<Return>", lambda _: self.show_file(True))
        self.tree.bind("<Home>", lambda _: self.tree_select_pos(0))
        self.tree.bind("<End>", lambda _: self.tree_select_pos(-1))
        self.tree.bind("<Next>", lambda _: self.tree_select_move(10))
        self.tree.bind("<Prior>", lambda _: self.tree_select_move(-10))

        self.bind("<Escape>", self.app_exit)

        self.protocol("WM_DELETE_WINDOW", self.app_exit)  # arrêter le programme quand fermeture de la fenêtre

    # ------------------------------------------------------------------------------------------
    # Mise à jour interface
    # ------------------------------------------------------------------------------------------
    def set_cols_to_display(self) -> list:
        cols_grp: dict[str, dict] = self._tree_cols_grp()

        display_cols: list[str] = list(cols_grp["common"].keys())
        if self.stats_mode:
            cols_to_add = list(cols_grp["stats"].keys())
        else:
            cols_to_add = list(cols_grp["queries"].keys())

        display_cols.extend(cols_to_add)
        self.tree["displaycolumns"] = display_cols

    def get_cols_to_update(self) -> dict:
        cols_grp: dict[str, dict] = self._tree_cols_grp()
        cols: dict = cols_grp["common"]
        if self.stats_mode:
            cols.update(cols_grp["stats"])
        else:
            cols.update(cols_grp["queries"])
            cols.update(cols_grp["queries_hidden"])

        return cols

    def tree_refresh(self):
        self.tree.delete(*self.tree.get_children())
        self.set_cols_to_display()

        cols_to_update: dict = self.get_cols_to_update()
        rows_to_insert: list[logs_user.LogRecord | logs_user.LogStats] = []
        rows_to_insert = (
            self.user_db.get_stats() if self.stats_mode else self.user_db.get_last_records(self.query_name)
        )

        count: int = 0
        for rows in rows_to_insert:
            count += 1
            iid = count if self.stats_mode else rows.id
            self.tree.insert("", tk.END, iid=iid)

            for col, val in cols_to_update.items():
                if col == "num":
                    value = count
                else:
                    value = getattr(rows, val["attr"], "")

                # Formatage des valeurs
                if col in ("date", "last_date"):
                    value = datetime.strftime(value, DATE_FORMAT)
                elif col in ("time", "last_time"):
                    value = datetime.strftime(value, TIME_FORMAT)
                elif col in ("duration", "min_run", "max_run"):
                    value = self.duration_format(value)
                elif col == "file":
                    nb_rows = getattr(rows, "nb_rows", "")
                    value = "" if nb_rows == 0 or Path(value).exists() else "\U0000274c"
                elif col in ("num", "nb_rows", "nb_run"):
                    value = format(value, ",").replace(",", " ")

                value = value if value else ""
                self.tree.set(iid, column=col, value=value)

        self.show_query_stats()

    def tree_select_move(self, offset: int):
        curr_item = self.tree.selection()
        if not curr_item == ():
            pos = self.tree.index(curr_item[0])
        else:
            pos = 0

        self.tree_select_pos(pos + offset)

    def tree_select_pos(self, pos: int):
        max_pos = len(self.tree.get_children()) - 1

        if max_pos < 0:
            return
        elif pos < 0 or pos > max_pos:
            pos = max_pos
        elif pos < 0:
            pos = 0

        item = self.tree.get_children()[pos]
        self.tree.selection_set(item)
        self.tree.focus(item)
        self.tree.see(item)

    def tree_header_click(self, col: str):
        reverse = False
        if col == self.sort_info["col"] and self.sort_info["reverse"] is False:
            reverse = True

        self.tree_sort(col, reverse)

    def tree_sort(self, sort_col: str = "query", reverse: bool = False):
        # fonction récup valeur
        def get_col_value(key):
            value = self.tree.set(key, sort_col)
            if sort_col in ("num", "nb_rows", "nb_run"):
                value = int(value)
            return value

        # tri de la colonne
        items = list(self.tree.get_children(""))
        items.sort(reverse=reverse, key=lambda k: get_col_value(k))
        for i, item in enumerate(items):
            self.tree.move(item, "", i)

        # mise à jour info du dernier tri
        self.sort_info["col"] = sort_col
        self.sort_info["reverse"] = reverse

    def show_query_stats(self):
        stats_lst: list
        stats_txt: str = ""

        item = self.tree.selection()
        if item:
            query = self.tree.item(item[0])["values"][self.col_idx("query")]
        else:
            query = self.query_name

        stats_lst = self.user_db.get_stats(query)
        if stats_lst and len(stats_lst) == 1:
            stats: logs_user.LogStats = stats_lst[0]
            stats_txt = f"Nombre d'execution : {stats.nb_run}"
            stats_txt += f"\nDurée mini : {self.duration_format(stats.min_run)}"
            stats_txt += f"\nDurée maxi : {self.duration_format(stats.max_run)}"
            stats_txt += f"\nDernière execution : le {datetime.strftime(stats.last_run, DATE_FORMAT)}"
            stats_txt += f" à {datetime.strftime(stats.last_run, TIME_FORMAT)}"

        self.textbox["state"] = "normal"
        self.textbox.replace("1.0", "end", stats_txt)
        self.textbox["state"] = "disabled"

    def show_query_params(self):
        item = self.tree.selection()
        params_raw: dict = {}
        json_txt: str = ""

        if self.stats_mode:
            self.show_query_stats()
            return

        if item:
            json_txt = self.tree.item(item[0])["values"][self.col_idx("parameters")]

        if json_txt:
            params_raw: dict = json.loads(json_txt)
            len_max = len(max([d["description"] for d in params_raw.values()], key=len))

        params_lst = []
        for _, v in params_raw.items():
            p: str = v["description"]
            p = p.ljust(len_max) + " : " + v["val_display"]
            params_lst.append(p)

        params_txt = "\n".join(params_lst)

        self.textbox["state"] = "normal"
        self.textbox.replace("1.0", "end", params_txt)
        self.textbox["state"] = "disabled"

    def show_all_queries(self):
        if self.stats_mode:
            return
        if not self.query_name and not self.saved_query_name:
            return

        if self.saved_query_name:
            self.query_name = self.saved_query_name
            self.saved_query_name = ""
            self.menu_extract.entryconfig(f"Revenir à {self.query_name}", label="Afficher tout")
        else:
            self.saved_query_name = self.query_name
            self.query_name = ""
            self.menu_extract.entryconfig("Afficher tout", label=f"Revenir à {self.saved_query_name}")

        self.tree_refresh()

    def show_all_stats(self):
        if self.stats_mode:
            self.stats_mode = False
            self.menu_extract.entryconfig("Vue requêtes", label="Vue stats")
        else:
            self.stats_mode = True
            self.menu_extract.entryconfig("Vue stats", label="Vue requêtes")

        self.tree_refresh()

    def app_exit(self, _: Event = None):
        self.destroy()
        if self.parent is None:
            self.quit()

    # ------------------------------------------------------------------------------------------
    # Autres traitements
    # ------------------------------------------------------------------------------------------
    def duration_format(self, secs: float) -> str:
        min = int(secs // 60)
        sec = str(round(secs % 60, 0)).split(".")[0].rjust(2, "0")
        text = f"{min} min {sec}"
        return text

    def show_file(self, only_reveal: bool = False):
        file: Path = self.get_extract_path()
        if not file:
            return
        elif not file.exists():
            messagebox.showerror("Erreur", "Le fichier n'existe pas", parent=self)
        elif only_reveal:
            subprocess.Popen(f"explorer /select,{file}")
        else:
            startfile(file)

    def save_as(self):
        src: Path = self.get_extract_path()
        save_as(self, src)

    def get_extract_path(self) -> Path | None:
        item = self.tree.selection()
        file: Path
        if item:
            file = Path(self.tree.item(item[0])["values"][self.col_idx("fullpath")])

        return file

    def col_idx(self, col_id: str):
        return list(self._tree_cols_all()).index(col_id)


if __name__ == "__main__":
    my_app = LogsWindow()
    my_app.mainloop()
