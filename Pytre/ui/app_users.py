import tkinter as tk
from tkinter import ttk, Event, font, messagebox, filedialog

if not __package__:
    import syspath_insert  # noqa: F401  # disable unused-import warning

from ui.InputDialog import InputDialog
from settings import User
from about import APP_NAME


class UsersWindow(tk.Toplevel):
    def __init__(self, parent=None):
        super().__init__()
        self.parent = parent
        self.focus_set() if self.parent else self.master.withdraw()

        self.detached_items: set[str] = set()
        self.filter_var: tk.StringVar

        self.default_filter_cols = ["username", "title", "grp_authorized", "x3_id"]
        self.default_sort_col: str = "title"
        self.sort_info: dict[str, str | bool] = {"col": None, "reverse": None}
        self.groups: set = set()

        self._setup_ui()
        self._events_binds()

        self.tree_refresh()

    # ------------------------------------------------------------------------------------------
    # Création de l'interface
    # ------------------------------------------------------------------------------------------
    def _setup_ui(self):
        self.title(f"{APP_NAME} - Gestion des utilisateurs")
        self.geometry("1200x800+100+75")
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
        self.menubar = tk.Menu(self, tearoff=False)

        self.config(menu=self.menubar)

        menu_users = tk.Menu(self.menubar, tearoff=False)
        menu_users.add_command(label="Ajouter...", command=self.user_add)
        menu_users.add_command(label="Modifier...", command=self.user_modify)
        menu_users.add_command(label="Supprimer...", command=self.user_delete)
        menu_users.add_separator()
        menu_users.add_command(label="Importer...", command=self.import_users)
        menu_users.add_command(label="Exporter...", command=self.export_users)
        menu_users.add_separator()
        menu_users.add_command(label="Recharger", command=self.tree_refresh)
        self.menubar.add_cascade(label="Utilisateurs", menu=menu_users)

        menu_groups = tk.Menu(self.menubar, tearoff=False)
        # menu_groups.add_command(label="Gestion...", command=None)
        # menu_groups.add_separator()
        menu_groups.add_command(label="Affecter à la sélection...", command=self.groups_add)
        menu_groups.add_command(label="Retirer de la sélection...", command=self.groups_remove)
        self.menubar.add_cascade(label="Groupes", menu=menu_groups)

    def _setup_ui_ctrl(self):
        self.btn_add = ttk.Button(self.ctrl_frame, text="Ajouter", command=self.user_add)
        self.btn_modify = ttk.Button(self.ctrl_frame, text="Modifier", command=self.user_modify)
        self.btn_delete = ttk.Button(self.ctrl_frame, text="Supprimer", command=self.user_delete)

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
            self.tree.heading(col, text=attr["text"], command=lambda c=col: self.tree_header_click(c))
            self.tree.column(col, minwidth=attr["minwidth"], stretch=attr["stretch"])
            if col in ["admin"]:
                self.tree.column(col, anchor=tk.CENTER)

        xbar = ttk.Scrollbar(self.tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(xscroll=xbar.set)
        ybar = ttk.Scrollbar(self.tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=ybar.set)

        self.tree.grid(row=0, column=0, columnspan=1, padx=2, pady=2, sticky="nswe")
        xbar.grid(row=1, column=0, sticky="we")
        ybar.grid(row=0, column=1, sticky="ns")

    def _tree_cols(self) -> dict:
        cols = {
            "username": {"text": "Id", "minwidth": 200, "stretch": False},
            "title": {"text": "Libellé", "minwidth": 200, "stretch": False},
            "admin": {"text": "Admin", "minwidth": 75, "stretch": False},
            "grp_authorized": {"text": "Groupes", "minwidth": 60, "stretch": True},
            "msg_login_cust": {"text": "Login Message", "minwidth": 100, "stretch": False},
        }

        for attr in User.get_cust_attribs_list():
            cols[attr] = {"text": attr, "minwidth": 60, "stretch": False}

        return cols

    # ------------------------------------------------------------------------------------------
    # Définition des évènements générer par les traitements
    # ------------------------------------------------------------------------------------------
    def _events_binds(self):
        self.filter.bind("<KeyRelease>", lambda _: self.tree_filter())

        self.tree.bind("<Home>", lambda e: self.tree_select_pos(0, e))
        self.tree.bind("<End>", lambda e: self.tree_select_pos(-1, e))
        self.tree.bind("<Next>", lambda e: self.tree_select_move(10, e))
        self.tree.bind("<Prior>", lambda e: self.tree_select_move(-10, e))
        self.tree.bind("<Double-Button-1>", lambda e: self.user_modify())
        self.tree.bind("<Button-3>", lambda e: self.menubar.post(e.x_root, e.y_root))

        self.protocol("WM_DELETE_WINDOW", self.app_exit)  # arrêter le programme quand fermeture de la fenêtre

    # ------------------------------------------------------------------------------------------
    # Mise à jour interface
    # ------------------------------------------------------------------------------------------
    def tree_filter(self, cols: list[str] = []):
        cols = cols if cols else self.default_filter_cols
        i = 0
        while i < len(cols):
            if cols[i] not in self.tree["columns"]:
                print(f'Error when filtering : "{cols[i]}" do not exists in tree columns')
                cols.pop(i)
            else:
                i += 1

        text_filter = self.filter_var.get()
        if text_filter == "" and self.detached_items == {}:
            return

        all_items = set(self.tree.get_children("")) | self.detached_items
        for item in all_items:
            hide = True
            for col in cols:
                value: str = self.tree.set(item, col)
                if text_filter == "" or value.lower().find(text_filter.lower()) != -1:
                    hide = False
                    break

            if hide and item not in self.detached_items:
                self.tree.detach(item)
                self.detached_items.add(item)
            elif not hide and item in self.detached_items:
                self.tree.move(item, "", tk.END)
                self.tree_sort(self.sort_info["col"], self.sort_info["reverse"])
                self.detached_items.remove(item)

    def tree_sort(self, sort_col: str = "", reverse: bool = False):
        sort_col = self.default_sort_col if sort_col == "" else sort_col

        # tri de la colonne
        items = list(self.tree.get_children(""))
        items.sort(reverse=reverse, key=lambda k: self.tree.set(k, sort_col))
        for i, item in enumerate(items):
            self.tree.move(item, "", i)

        # mise à jour nom des colonnes avec indication du tri
        cols = {sort_col, old_col} if (old_col := self.sort_info["col"]) else {sort_col}
        for col in cols:
            title = self._tree_cols()[col]["text"]
            if col == sort_col:
                self.sort_symbols = ("\U000025b3", "\U000025bd")  # self pour que self.tree_autosize() en tienne compte
                title = f"{title} {self.sort_symbols[0]}" if not reverse else f"{title} {self.sort_symbols[1]}"
            self.tree.heading(col, text=title)

        # mise à jour info du dernier tri
        self.sort_info["col"] = sort_col
        self.sort_info["reverse"] = reverse

    def tree_header_click(self, col: str):
        reverse = False
        if col == self.sort_info["col"] and self.sort_info["reverse"] is False:
            reverse = True

        self.tree_sort(col, reverse)

    def tree_refresh(self, sort_col: str = ""):
        sort_col = self.default_sort_col if sort_col == "" else sort_col

        users: list[User] = User.users_get_all()
        cols = self._tree_cols()

        self.tree.delete(*self.tree.get_children())
        self.tree.delete(*self.detached_items)
        self.detached_items = set()
        self.groups = set()

        cust_attribs: list = User.get_cust_attribs_list()
        for u in users:
            values: list = []
            for col in cols.keys():
                if col in cust_attribs:
                    value = u.attribs_cust.get(col, "")
                else:
                    value = getattr(u, col, "")

                if col == "grp_authorized":
                    value.remove("all")
                    value.sort()
                    self.groups.update(value)
                    value = "".join([f"[{item}]" for item in value])
                if col == "admin":
                    value = "\U0001f5f9" if value is True else "\U000000b7"
                values.append(value)

            try:
                self.tree.insert("", tk.END, iid=u.username, values=values)
            except Exception as e:
                messagebox.showerror("Erreur chargement liste", e)

        self.tree_sort(sort_col)
        self.tree_autosize()
        self.tree_filter()

    def tree_autosize(self):
        sort_max_width = max([font.Font().measure(item) for item in self.sort_symbols])

        tkfont = font.nametofont("TkTextFont")
        for col in self.tree["columns"]:
            max_width = tkfont.measure(self.tree.heading(col)["text"] + "    ") + sort_max_width
            for item in self.tree.get_children(""):
                item_width = tkfont.measure(self.tree.set(item, col) + "    ")
                max_width = max(max_width, item_width)
            self.tree.column(col, width=max_width)

    def tree_select_pos(self, pos: int, e: Event = None):
        if e is not None and e.widget is self.filter:  # désactivation si le filtre est en saisi
            return

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

    def tree_select_move(self, offset: int, e: Event = None):
        if e is not None and e.widget is self.filter:  # désactivation si le filtre est en saisi
            return

        curr_item = self.tree.selection()
        if not curr_item == ():
            pos = self.tree.index(curr_item[0])
        else:
            pos = 0

        self.tree_select_pos(pos + offset)

    def app_exit(self, _: Event = None):
        self.destroy()
        if self.parent is None:
            self.quit()

    # ------------------------------------------------------------------------------------------
    # Autres traitements
    # ------------------------------------------------------------------------------------------
    def user_add(self):
        UserDialog(self)

    def user_modify(self):
        items = self.tree.selection()
        if items:
            user: User = User(username=items[0])
            UserDialog(self, user)

    def user_delete(self):
        items = self.tree.selection()

        if not self._delete_confirm(items):
            return

        nb_ok, errors = 0, []
        for item in items:
            try:
                user = User(username=item)
                if user.exists:
                    user.delete()
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

    def groups_add(self):
        items = self.tree.selection()
        if not items:
            msg = "Vous devez sélectionner au moins 1 utilisateur pour utiliser cette fonction"
            messagebox.showwarning(title="Affectation groupes", message=msg, parent=self, type=messagebox.OK)
        else:
            GroupsDialog.add(self, items)

    def groups_remove(self):
        items = self.tree.selection()
        if not items:
            msg = "Vous devez sélectionner au moins 1 utilisateur pour utiliser cette fonction"
            messagebox.showwarning(title="Retirer groupes", message=msg, parent=self, type=messagebox.OK)
        else:
            GroupsDialog.remove(self, items)

    def import_users(self):
        title = "Fichier à importer"
        types = (("Fichier csv", "*.csv"), ("Tous les fichiers", "*.*"))
        filename = filedialog.askopenfilename(title=title, filetypes=types, parent=self)

        result = User.csv_import(filename)
        self.tree_refresh()
        if result:
            msg = "Fin de l'import des utilisateurs"
            messagebox.showinfo(title="Import", message=msg, parent=self, type=messagebox.OK)
        else:
            msg = "Quelque chose ne s'est pas bien passé lors de l'import !"
            messagebox.showerror(title="Import", message=msg, parent=self, type=messagebox.OK)

    def export_users(self):
        title = "Fichier à exporter"
        types = (("Fichier csv", "*.csv"),)
        filename = filedialog.asksaveasfilename(title=title, filetypes=types, defaultextension=".csv", parent=self)
        if not filename:
            return

        result = User.csv_export(filename, overwrite=True)
        if result:
            msg = "Fin de l'export des utilisateurs"
            messagebox.showinfo(title="Export", message=msg, parent=self, type=messagebox.OK)
        else:
            msg = "Quelque chose ne s'est pas bien passé lors de l'export !"
            messagebox.showerror(title="Export", message=msg, parent=self, type=messagebox.OK)


class UserDialog(tk.Toplevel):
    def __init__(self, parent, user: User = None):
        super().__init__(master=parent)
        self.parent: UsersWindow = parent
        self.user: User = user

        self.focus_set()
        self.parent.wm_attributes("-disabled", True)
        self.transient(self.parent)

        self._setup_ui()
        self._events_binds()

        if self.user:
            self.set_entries()

        self.groups_set()

    # ------------------------------------------------------------------------------------------
    # Création de l'interface
    # ------------------------------------------------------------------------------------------
    def _setup_ui(self):
        if not self.user:
            self.title(f"{APP_NAME} - Ajout utilisateur")
        else:
            self.title(f"{APP_NAME} - Modification utilisateur")

        self.minsize(width=400, height=100)
        self.geometry(f"+{self.parent.winfo_x() + 200}+{self.parent.winfo_y() + 150}")
        self.resizable(True, True)

        self.entries_frame = ttk.Frame(self, padding=1, borderwidth=2)
        self.groups_frame = ttk.Frame(self, padding=2, borderwidth=2)
        self.buttons_frame = ttk.Frame(self, padding=1, borderwidth=2)

        self.entries_frame.grid(row=0, column=0, padx=4, pady=4, sticky="nswe")
        self.groups_frame.grid(row=1, column=0, padx=4, pady=4, sticky="nswe")
        self.buttons_frame.grid(row=2, column=0, padx=4, pady=4, sticky="nswe")

        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

        self._setup_entries()
        self._setup_groups()
        self._setup_buttons()

    def _setup_entries(self):
        self.entries_frame.columnconfigure(1, weight=1)

        self.entries = {
            "username": {"text": "Id"},
            "title": {"text": "Libellé"},
            "admin": {"text": "Admin"},
            "msg_login_cust": {"text": "Login Message"},
        }
        for attr in User.get_cust_attribs_list():
            self.entries[attr] = {"text": attr}

        num_row = 0
        for key, item in self.entries.items():
            my_label = ttk.Label(self.entries_frame, text=item["text"] + " : ")

            if key == "admin":
                my_tk_var = tk.BooleanVar()
                my_entry = ttk.Checkbutton(self.entries_frame, variable=my_tk_var, onvalue=True, offvalue=False)
            else:
                my_tk_var = tk.StringVar()
                my_entry = ttk.Entry(self.entries_frame, textvariable=my_tk_var)
                if self.user and key == "username":
                    my_entry.config(state="disabled")

            new_key = {"w_label": my_label, "w_entry": my_entry, "var": my_tk_var}
            item.update(new_key)

            my_label.grid(row=num_row, column=0, padx=2, pady=2, sticky="nswe")
            my_entry.grid(row=num_row, column=1, padx=2, pady=2, sticky="nswe")
            num_row += 1

    def _setup_groups(self):
        self.groups_frame.columnconfigure(0, weight=1)
        self.groups_frame.rowconfigure(1, weight=1)

        title_label = ttk.Label(self.groups_frame, text="Groupes :")
        self.new_grp = ttk.Button(self.groups_frame, text="+", width=3, command=self.group_add)

        self.listbox = tk.Listbox(self.groups_frame, selectmode=tk.MULTIPLE, activestyle="none", relief="groove")
        ybar = ttk.Scrollbar(self.groups_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscroll=ybar.set)

        title_label.grid(row=0, column=0, padx=2, pady=2, sticky="nswe")
        self.new_grp.grid(row=0, column=1, padx=2, pady=2, sticky="nswe")
        self.listbox.grid(row=1, column=0, columnspan=2, padx=2, pady=2, sticky="nswe")
        ybar.grid(row=1, column=1, sticky="nse")

    def _setup_buttons(self):
        self.buttons_frame.columnconfigure(0, weight=1)

        self.btn_save = ttk.Button(self.buttons_frame, text="Enregistrer", command=self.user_save)
        self.btn_cancel = ttk.Button(self.buttons_frame, text="Annuler", command=self.close)

        self.btn_save.grid(row=0, column=1, padx=2, pady=2, sticky="nse")
        self.btn_cancel.grid(row=0, column=2, padx=2, pady=2, sticky="nse")

    # ------------------------------------------------------------------------------------------
    # Définition des évènements générer par les traitements
    # ------------------------------------------------------------------------------------------
    def _events_binds(self):
        self.protocol("WM_DELETE_WINDOW", self.close)  # arrêter le programme quand fermeture de la fenêtre

    # ------------------------------------------------------------------------------------------
    # Autres traitements
    # ------------------------------------------------------------------------------------------
    def set_entries(self):
        for key, item in self.entries.items():
            if key in User.get_cust_attribs_list():
                val = self.user.attribs_cust.get(key, "")
            else:
                val = getattr(self.user, key, "")
            item["var"].set(val)

    def groups_set(self):
        for group in sorted(self.parent.groups):
            self.listbox.insert(tk.END, group)

        if self.user:
            for i, line in enumerate(self.listbox.get(0, tk.END)):
                if line in self.user.grp_authorized:
                    self.listbox.selection_set(i)

    def group_add(self):
        curr_groups = self.listbox.get(0, tk.END)
        new_group = InputDialog.ask("Nouveau groupe", "Groupe à ajouter :", parent=self)
        if new_group and new_group not in curr_groups:
            pos = 0
            for line in curr_groups:
                if new_group > line:
                    pos += 1
                else:
                    break

            self.listbox.insert(pos, new_group)
            self.listbox.selection_set(pos)

    def user_save(self):
        username = self.entries["username"]["var"].get()

        if self.user:
            user = self.user
        else:
            u_entry = User.find_user_entry(username)
            if u_entry:
                msg = "Utilisateur déjà existant !"
                messagebox.showerror(title="Erreur ajout", message=msg, parent=self, type=messagebox.OK)
                return
            user: User = User(detect_user=False)

        for key, item in self.entries.items():
            val = item["var"].get()
            if key in User.get_cust_attribs_list():
                user.attribs_cust[key] = val
            else:
                setattr(user, key, val)

        user.grp_authorized = [self.listbox.get(line) for line in self.listbox.curselection()]

        user.save()
        self.close(refresh_tree=True)

    def close(self, _: Event = None, refresh_tree: bool = False):
        self.parent.wm_attributes("-disabled", False)

        if refresh_tree:
            self.parent.tree_refresh()
        self.parent.focus_set()

        self.destroy()


class GroupsDialog(tk.Toplevel):
    @classmethod
    def add(cls, parent: UserDialog, usernames: list[str]):
        dialog = GroupsDialog(parent=parent, usernames=usernames, remove_mode=False)
        cls.wait_window(dialog)
        return

    @classmethod
    def remove(cls, parent: UserDialog, usernames: list[str]):
        dialog = GroupsDialog(parent=parent, usernames=usernames, remove_mode=True)
        cls.wait_window(dialog)
        return

    def __init__(self, parent, usernames: list[str], remove_mode: bool = False):
        super().__init__(master=parent)
        self.parent: UsersWindow = parent
        self.usernames: list[str] = usernames

        self.focus_set()
        self.parent.wm_attributes("-disabled", True)
        self.transient(self.parent)

        self.remove_mode: bool = remove_mode

        self._setup_ui()
        self._events_binds()

        self.groups_set()

    # ------------------------------------------------------------------------------------------
    # Création de l'interface
    # ------------------------------------------------------------------------------------------
    def _setup_ui(self):
        nb_txt = "(1 utilisateur)" if len(self.usernames) <= 1 else f"({len(self.usernames)} utilisateurs)"
        if not self.remove_mode:
            self.title(f"{APP_NAME} - Affecter groupes {nb_txt}")
        else:
            self.title(f"{APP_NAME} - Retirer groupes {nb_txt}")

        self.minsize(width=400, height=100)
        self.geometry(f"+{self.parent.winfo_x() + 200}+{self.parent.winfo_y() + 150}")
        self.resizable(True, True)

        self.groups_frame = ttk.Frame(self, padding=2, borderwidth=2)
        self.buttons_frame = ttk.Frame(self, padding=2, borderwidth=2)

        self.groups_frame.grid(row=0, column=0, padx=0, pady=0, sticky="nswe")
        self.buttons_frame.grid(row=1, column=0, padx=0, pady=0, sticky="nswe")

        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self._setup_groups()
        self._setup_buttons()

    def _setup_groups(self):
        self.groups_frame.columnconfigure(0, weight=1)
        self.groups_frame.rowconfigure(1, weight=1)

        if not self.remove_mode:
            title_text = "Sélection des groupes à affecter :"
            select_bg_color = "#B4FFB4"
        else:
            title_text = "Sélection des groupes à retirer :"
            select_bg_color = "#FFC8C8"

        title_label = ttk.Label(self.groups_frame, text=title_text)
        self.new_grp = ttk.Button(self.groups_frame, text="+", width=3, command=self.group_add)

        self.listbox = tk.Listbox(self.groups_frame, selectmode=tk.MULTIPLE, activestyle="none", relief="groove")
        ybar = ttk.Scrollbar(self.groups_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscroll=ybar.set)
        self.listbox.configure(selectbackground=select_bg_color, selectforeground="black")

        title_label.grid(row=0, column=0, padx=2, pady=2, sticky="nswe")
        if not self.remove_mode:
            self.new_grp.grid(row=0, column=1, padx=2, pady=2, sticky="nswe")
        self.listbox.grid(row=1, column=0, columnspan=2, padx=2, pady=2, sticky="nswe")
        ybar.grid(row=1, column=1, sticky="nse")

    def _setup_buttons(self):
        self.buttons_frame.columnconfigure(0, weight=1)

        save_txt = "Ajouter" if not self.remove_mode else "Retirer"
        self.btn_save = ttk.Button(self.buttons_frame, text=save_txt, command=self.users_update_groups)
        self.btn_cancel = ttk.Button(self.buttons_frame, text="Annuler", command=self.close)

        self.btn_save.grid(row=0, column=1, padx=2, pady=2, sticky="nse")
        self.btn_cancel.grid(row=0, column=2, padx=2, pady=2, sticky="nse")

    # ------------------------------------------------------------------------------------------
    # Définition des évènements générer par les traitements
    # ------------------------------------------------------------------------------------------
    def _events_binds(self):
        self.protocol("WM_DELETE_WINDOW", self.close)  # arrêter le programme quand fermeture de la fenêtre

    # ------------------------------------------------------------------------------------------
    # Autres traitements
    # ------------------------------------------------------------------------------------------
    def groups_set(self):
        for group in sorted(self.parent.groups):
            self.listbox.insert(tk.END, group)

    def group_add(self):
        curr_groups = self.listbox.get(0, tk.END)
        new_group = InputDialog.ask("Nouveau groupe", "Groupe à ajouter :", parent=self)
        if new_group and new_group not in curr_groups:
            pos = 0
            for line in curr_groups:
                if new_group > line:
                    pos += 1
                else:
                    break

            self.listbox.insert(pos, new_group)
            self.listbox.selection_set(pos)

    def users_update_groups(self):
        groups = [self.listbox.get(line) for line in self.listbox.curselection()]

        if not groups:
            action = "ajouter" if not self.remove_mode else "retirer"
            msg = f"Aucun groupe à {action} sélectionné !"
            messagebox.showerror(title="Erreur modification", message=msg, parent=self, type=messagebox.OK)
            return

        if not self.remove_mode:
            User.users_add_groups(self.usernames, groups)
        else:
            User.users_remove_groups(self.usernames, groups)

        self.close(refresh_tree=True)

    def close(self, _: Event = None, refresh_tree: bool = False):
        self.parent.wm_attributes("-disabled", False)

        if refresh_tree:
            self.parent.tree_refresh()
        self.parent.focus_set()

        self.destroy()


if __name__ == "__main__":
    my_app = UsersWindow()
    my_app.mainloop()
