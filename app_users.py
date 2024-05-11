import tkinter as tk
from tkinter import ttk, Event, font, messagebox

from settings import Settings, User


class UserWindow(tk.Toplevel):
    def __init__(self, parent=None):
        super().__init__()
        self.parent = parent
        self.focus_set() if self.parent else self.master.withdraw()

        self.settings: Settings = Settings()
        self.detached_items: list[str] = []
        self.filter_var: tk.StringVar

        self.default_filter_cols = ["domain_and_name", "title"]
        self.default_sort = "title"

        self._setup_ui()
        self._events_binds()
        self.tree_refresh()

    # ------------------------------------------------------------------------------------------
    # Définition des styles
    # ------------------------------------------------------------------------------------------
    def setup_style(self):
        self.style_frame_label = "Bold.TLabelFrame.Label"
        ttk.Style().configure(self.style_frame_label, font=("TkDefaultFont", 10, "bold"))

    # ------------------------------------------------------------------------------------------
    # Création de l'interface
    # ------------------------------------------------------------------------------------------
    def _setup_ui(self):
        self.title("Gestion des utilisateurs")
        self.minsize(width=1200, height=800)
        self.maxsize(width=int(self.winfo_screenwidth() * 0.90), height=int(self.winfo_screenheight() * 0.90))
        self.resizable(True, True)

        self.ctrl_frame = ttk.Frame(self, padding=1, borderwidth=2)
        self.tree_frame = ttk.Frame(self, padding=1, borderwidth=2)

        self.ctrl_frame.grid(row=0, column=0, padx=0, pady=0, sticky="we")
        self.tree_frame.grid(row=1, column=0, padx=0, pady=0, sticky="nswe")

        self._setup_ui_menu()
        self._setup_ui_ctrl()
        self._setup_ui_tree()

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

    def _setup_ui_menu(self):
        menubar = tk.Menu(self)

        self.config(menu=menubar)

        menu_file = tk.Menu(menubar, tearoff=False)
        menu_file.add_command(label="Ajouter...", command=self.add_user)
        menu_file.add_command(label="Modifier...", command=self.modify_user)
        menu_file.add_command(label="Supprimer...", command=self.delete_user)
        menu_file.add_separator()
        menu_file.add_command(label="Importer...", command=self.import_users)
        menu_file.add_command(label="Exporter...", command=self.export_users)
        menu_file.add_separator()
        menu_file.add_command(label="Recharger", command=self.tree_refresh)
        menubar.add_cascade(label="Utilisateurs", menu=menu_file)

    def _setup_ui_ctrl(self):
        self.btn_add = ttk.Button(self.ctrl_frame, text="Ajouter", command=self.add_user)
        self.btn_modify = ttk.Button(self.ctrl_frame, text="Modifier", command=self.modify_user)
        self.btn_delete = ttk.Button(self.ctrl_frame, text="Supprimer", command=self.delete_user)

        filter_label = ttk.Label(self.ctrl_frame, text="Filtre :")
        self.filter_var = tk.StringVar()
        self.filter = ttk.Entry(self.ctrl_frame, textvariable=self.filter_var)

        self.btn_add.grid(row=0, column=0, padx=2, pady=2, sticky="nswe")
        self.btn_modify.grid(row=0, column=1, padx=2, pady=2, sticky="nswe")
        self.btn_delete.grid(row=0, column=2, padx=2, pady=2, sticky="nswe")
        filter_label.grid(row=0, column=3, padx=2, pady=2, sticky="nswe")
        self.filter.grid(row=0, column=4, padx=2, pady=2, sticky="nswe")

        self.ctrl_frame.grid_rowconfigure(0, weight=1)
        self.ctrl_frame.grid_columnconfigure(4, weight=1)

    def _setup_ui_tree(self):
        self.tree_frame.grid_rowconfigure(0, weight=1)
        self.tree_frame.grid_columnconfigure(0, weight=1)

        cols = self._tree_cols()
        self.tree = ttk.Treeview(self.tree_frame, columns=list(cols.keys()), show="headings", selectmode="extended")
        for col, attr in cols.items():
            self.tree.heading(col, text=attr["text"])
            self.tree.column(col, minwidth=attr["minwidth"], stretch=attr["stretch"])
            if col in ["superuser"]:
                self.tree.column(col, anchor=tk.CENTER)

        xbar = ttk.Scrollbar(self.tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(xscroll=xbar.set)
        ybar = ttk.Scrollbar(self.tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=ybar.set)

        self.tree.grid(row=0, column=0, columnspan=1, padx=2, pady=2, sticky="nswe")
        xbar.grid(row=1, column=0, sticky="we")
        ybar.grid(row=0, column=1, sticky="ns")

    def _tree_cols(self) -> dict:
        return {
            "domain_and_name": {"text": "User", "minwidth": 200, "stretch": False},
            "title": {"text": "Libellé", "minwidth": 200, "stretch": False},
            "superuser": {"text": "Admin", "minwidth": 75, "stretch": False},
            "grp_authorized": {"text": "Groupes", "minwidth": 60, "stretch": True},
            "x3_id": {"text": "Id X3", "minwidth": 60, "stretch": False},
            "msg_login_cust": {"text": "Login Message", "minwidth": 100, "stretch": False},
        }

    # ------------------------------------------------------------------------------------------
    # Définition des évènements générer par les traitements
    # ------------------------------------------------------------------------------------------
    def _events_binds(self):
        self.filter.bind("<KeyRelease>", lambda _: self.tree_filter())

        self.bind("<Home>", lambda e: self.tree_select_pos(0, e))
        self.bind("<End>", lambda e: self.tree_select_pos(-1, e))
        # self.bind("<Next>", lambda e: self.tree_select_move(10, e))
        # self.bind("<Prior>", lambda e: self.tree_select_move(-10, e))

        self.protocol("WM_DELETE_WINDOW", self.app_exit)  # arrêter le programme quand fermeture de la fenêtre

    # ------------------------------------------------------------------------------------------
    # Mise à jour interface
    # ------------------------------------------------------------------------------------------
    def tree_filter(self, cols: list[str] = []):
        cols = cols if cols else self.default_filter_cols

        text_filter = self.filter_var.get()
        if text_filter == "" and self.detached_items == []:
            return

        all_items = list(self.tree.get_children("")) + self.detached_items
        for item in all_items:
            hide = True
            for col in cols:
                value: str = self.tree.set(item, col)
                if text_filter == "" or value.lower().find(text_filter.lower()) != -1:
                    hide = False
                    break

            if hide and item not in self.detached_items:
                self.tree.detach(item)
                self.detached_items.append(item)
            elif not hide and item in self.detached_items:
                self.tree.move(item, "", tk.END)
                self.tree_sort("title")
                self.detached_items.remove(item)

    def tree_sort(self, sort_col: str = ""):
        sort_col = self.default_sort if sort_col == "" else sort_col
        items = list(self.tree.get_children(""))
        items.sort(key=lambda k: self.tree.set(k, sort_col))
        for i, item in enumerate(items):
            self.tree.move(item, "", i)

    def tree_refresh(self, sort_col: str = ""):
        sort_col = self.default_sort if sort_col == "" else sort_col

        users: list[User] = self.settings.users_get_all()
        cols = self._tree_cols()

        self.tree.delete(*self.tree.get_children())
        self.tree.delete(*self.detached_items)
        self.detached_items = []

        for u in users:
            values: list = []
            for col in cols.keys():
                value = getattr(u, col, "")
                if col in ["superuser"]:
                    value = "\U0001F5F9" if value is True else "\U000000B7"
                values.append(value)

            self.tree.insert("", tk.END, iid=u.domain_and_name, values=values)

        self.tree_sort(sort_col)
        self.tree_autosize()
        self.tree_filter()

    def tree_autosize(self):
        for col in self.tree["columns"]:
            max_width = font.Font().measure(self.tree.heading(col)["text"])
            for item in self.tree.get_children(""):
                item_width = font.Font().measure(self.tree.set(item, col))
                max_width = max(max_width, item_width)
            self.tree.column(col, width=max_width)
            # print(max_width)

    def tree_select_pos(self, pos: int, e: Event = None):
        if e is not None and e.widget is self.filter:  # désactivation si le filtre est en saisi
            return

        item = self.tree.get_children()[pos]
        self.tree.selection_set(item)
        self.tree.see(item)

    def tree_select_move(self, offset: int, e: Event = None):
        if e is not None and e.widget is self.filter:  # désactivation si le filtre est en saisi
            return

        curr_item = self.tree.selection()
        if not curr_item == ():
            curr_item = curr_item[0]
        pos = [i for i, item in enumerate(self.tree.get_children()) if item == curr_item]

        max_pos = len(self.tree.get_children()) - 1
        new_pos = pos[0] + offset if not pos == [] else offset
        new_pos = min(max(0, new_pos), max_pos)

        self.tree_select_pos(new_pos)

    def app_exit(self, _: Event = None):
        self.destroy()
        if self.parent is None:
            self.quit()

    # ------------------------------------------------------------------------------------------
    # Autres traitements
    # ------------------------------------------------------------------------------------------
    def add_user(self):
        messagebox.showinfo(title="Fonction", message="ajouter", parent=self)

    def modify_user(self):
        items = self.tree.selection()
        msg = f"{len(items)} utilisateur(s) :\n- " + "\n- ".join(items)
        messagebox.showinfo(title="Fonction", message=f"Modif de {msg}", parent=self)

    def delete_user(self):
        items = self.tree.selection()

        if not self._delete_confirm(items):
            return

        nb_ok, errors = 0, []
        for item in items:
            try:
                self.settings.user_delete(item)
                self.tree.delete(item)
                nb_ok += 1
            except LookupError:
                errors.append(item)

        self._delete_after_msg(nb_ok, errors)

    def _delete_confirm(self, items: list[str]) -> bool:
        if len(items) == 0:
            return False
        elif len(items) == 1:
            msg = f"Confirmation de la suppression de :\n{items[0]}"
        else:
            msg = f"Confirmation de la suppression de {len(items)} utilisateurs"

        if "ok" != messagebox.showwarning(title="Suppression", message=msg, parent=self, type=messagebox.OKCANCEL):
            return False

        return True

    def _delete_after_msg(self, nb_ok: int, errors: list[str]):
        if nb_ok == 0:
            msg = "Aucun utilisateur n'a pu être supprimé"
        elif nb_ok == 1:
            msg = "1 utilisateur a été supprimé"
        else:
            msg = f"{nb_ok} utilisateurs ont été supprimés"

        if len(errors) == 1 and nb_ok:
            msg = msg + f" et {len(errors)} n'a pas pu l'être :\n{errors[0]}"
        elif len(errors) > 1 and nb_ok:
            msg = msg + f" et {len(errors)} n'ont pas pas pu l'être :\n- " + "\n- ".join(errors)

        messagebox.showinfo(title="Suppression", message=msg, parent=self)

    def import_users(self):
        messagebox.showinfo(title="Fonction", message="importer", parent=self)

    def export_users(self):
        messagebox.showinfo(title="Fonction", message="exporter", parent=self)


if __name__ == "__main__":
    my_app = UserWindow()
    my_app.mainloop()
