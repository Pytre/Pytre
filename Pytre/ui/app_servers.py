import tkinter as tk
from tkinter import ttk, Event, filedialog, messagebox, font

if not __package__:
    import syspath_insert  # noqa: F401  # disable unused-import warning

from ui.InputDialog import InputDialog
from servers import Servers, Server, ServerType
from about import APP_NAME


class ServersWindow(tk.Toplevel):
    def __init__(self, parent=None):
        super().__init__()
        self.parent = parent
        if self.parent:
            self.focus_set()
            self.parent.wm_attributes("-disabled", True)
            self.transient(self.parent)
        else:
            self.master.withdraw()

        self.servers = Servers()
        self.servers.get_all_servers()
        self.type_server: tuple[str] = tuple([type.value for type in ServerType])
        self.groups: set[str] = set()

        self.server: Server = None
        self.new_server: bool = False

        self._setup_ui()
        self._events_binds()

        self.reload_all()

    # ------------------------------------------------------------------------------------------
    # Création de l'interface
    # ------------------------------------------------------------------------------------------
    def _setup_ui(self):
        self.title(f"{APP_NAME} - Gestion des serveurs")

        self.minsize(width=400, height=100)
        if self.parent:
            self.geometry(f"+{self.parent.winfo_x() + 100}+{self.parent.winfo_y() + 50}")
        else:
            self.geometry("+200+150")
        self.resizable(True, True)

        self.tree_frame = ttk.Frame(self, padding=1, borderwidth=2)
        self.entries_frame = ttk.Frame(self, padding=1, borderwidth=2)
        self.groups_frame = ttk.Frame(self, padding=2, borderwidth=2)
        self.buttons_frame = ttk.Frame(self, padding=1, borderwidth=2)

        self.tree_frame.grid(row=0, column=0, rowspan=3, padx=4, pady=4, sticky="nswe")
        self.entries_frame.grid(row=0, column=1, padx=4, pady=4, sticky="nswe")
        self.groups_frame.grid(row=1, column=1, padx=4, pady=4, sticky="nswe")
        self.buttons_frame.grid(row=2, column=1, padx=4, pady=4, sticky="nswe")

        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=2)
        self.columnconfigure(1, weight=1)

        self._setup_ui_menu()
        self._setup_tree()
        self._setup_type()
        self._setup_entries()
        self._setup_groups()
        self._setup_buttons()

    def _setup_ui_menu(self):
        self.menubar = tk.Menu(self, tearoff=False)

        self.config(menu=self.menubar)

        menu_servers = tk.Menu(self.menubar, tearoff=False)
        menu_servers.add_command(label="Nouveau...", command=self.server_new)
        menu_servers.add_command(label="Supprimer...", command=self.server_remove)
        menu_servers.add_separator()
        menu_servers.add_command(label="Importer...", command=self.import_servers)
        menu_servers.add_command(label="Exporter...", command=self.export_servers)
        menu_servers.add_separator()
        menu_servers.add_command(label="Recharger", command=lambda: self.reload_all(True))
        self.menubar.add_cascade(label="Serveurs", menu=menu_servers)

    def _tree_cols(self) -> dict[str, dict]:
        return {
            "id": {"text": "Id", "width": 50, "stretch": False},
            "description": {"text": "Description", "width": 200, "stretch": True},
        }

    def _setup_tree(self):
        cols = self._tree_cols()

        self.tree = ttk.Treeview(self.tree_frame, columns=list(cols.keys()), show="headings", selectmode="browse")

        for col, attr in cols.items():
            self.tree.heading(col, text=attr["text"])
            self.tree.column(col, width=attr["width"], stretch=attr["stretch"])

        xbar = ttk.Scrollbar(self.tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(xscroll=xbar.set)
        ybar = ttk.Scrollbar(self.tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=ybar.set)

        self.tree.grid(row=0, column=0, columnspan=1, padx=2, pady=2, sticky="nswe")
        xbar.grid(row=1, column=0, sticky="we")
        ybar.grid(row=0, column=1, sticky="ns")

        self.tree_frame.rowconfigure(0, weight=1)
        self.tree_frame.columnconfigure(0, weight=1)

    def _setup_type(self):
        my_label = ttk.Label(self.entries_frame, text="type : ")
        self.type_box = ttk.Combobox(self.entries_frame, state="readonly", values=self.type_server)
        if len(self.type_server) <= 1:
            self.type_box["state"] = "disable"

        my_label.grid(row=0, column=0, padx=2, pady=2, sticky="nswe")
        self.type_box.grid(row=0, column=1, columnspan=2, padx=2, pady=2, sticky="nswe")

    def _setup_entries(self):
        self.entries = {}
        num_row = 1
        for key in self.servers.cols_std + self.servers.cols_cust:
            if key in ("uuid", "type", "grp_authorized"):
                continue

            my_label = ttk.Label(self.entries_frame, text=key + " : ")
            my_tk_var = tk.StringVar()
            my_entry = ttk.Entry(self.entries_frame, textvariable=my_tk_var)

            self.entries[key] = {"w_label": my_label, "w_entry": my_entry, "var": my_tk_var}

            my_label.grid(row=num_row, column=0, padx=2, pady=2, sticky="nswe")

            if key == "password":
                my_entry["width"] = 40
                my_entry.grid(row=num_row, column=1, padx=2, pady=2, sticky="nswe")
                reveal_button = ttk.Button(self.entries_frame, width=8)
                reveal_button.config(command=lambda wc=reveal_button, wt=my_entry: self.toggle_password(wc, wt))
                self.toggle_password(reveal_button, my_entry)
                reveal_button.grid(row=num_row, column=2, padx=2, pady=2, sticky="nswe")
            else:
                my_entry.grid(row=num_row, column=1, columnspan=2, padx=2, pady=2, sticky="nswe")

            num_row += 1

        self.entries_frame.columnconfigure(1, weight=1)

    def _setup_groups(self):
        self.groups_frame.columnconfigure(0, weight=1)
        self.groups_frame.rowconfigure(1, weight=1)

        title_label = ttk.Label(self.groups_frame, text="Restriction groupes :")
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

        self.btn_new = ttk.Button(self.buttons_frame, text="Nouveau", command=self.server_new)
        self.btn_save = ttk.Button(self.buttons_frame, text="Enregistrer", command=self.server_save)
        self.btn_remove = ttk.Button(self.buttons_frame, text="Supprimer", command=self.server_remove)
        self.btn_cancel = ttk.Button(self.buttons_frame, text="Fermer", command=self.app_exit)

        self.btn_new.grid(row=0, column=1, padx=2, pady=2, sticky="nse")
        self.btn_save.grid(row=0, column=2, padx=2, pady=2, sticky="nse")
        self.btn_remove.grid(row=0, column=3, padx=2, pady=2, sticky="nse")
        self.btn_cancel.grid(row=0, column=4, padx=2, pady=2, sticky="nse")

    # ------------------------------------------------------------------------------------------
    # Définition des évènements générer par les traitements
    # ------------------------------------------------------------------------------------------
    def _events_binds(self):
        self.tree.bind("<<TreeviewSelect>>", self.tree_selection_change)

        self.tree.bind("<Home>", lambda _: self.tree_select_pos(0))
        self.tree.bind("<End>", lambda _: self.tree_select_pos(-1))
        self.tree.bind("<Next>", lambda _: self.tree_select_move(10))
        self.tree.bind("<Prior>", lambda _: self.tree_select_move(-10))

        self.protocol("WM_DELETE_WINDOW", self.app_exit)  # arrêter le programme quand fermeture de la fenêtre

    # ------------------------------------------------------------------------------------------
    # Mise à jour interface
    # ------------------------------------------------------------------------------------------
    def reload_all(self, notify_end: bool = False):
        self.servers.get_all_servers(reload=True)
        self.tree_refresh(notify_end)

    def tree_refresh(self, notify_end: bool = False):
        selected_item_uuid = self.server.uuid if self.server else None

        for item in self.tree.get_children():
            self.tree.delete(item)

        item_to_select = None
        for _, server in self.servers.servers_dict.items():
            self.tree.insert("", tk.END, values=(server.id, server.description), iid=id(server))
            if selected_item_uuid and server.uuid == selected_item_uuid:
                item_to_select = (id(server),)

        self.tree_autosize()
        if item_to_select:
            self.tree.selection_set(item_to_select)
            self.tree.focus(item_to_select)
            self.tree.see(item_to_select)
        else:
            self.tree_select_pos(0)

        if notify_end:
            msg = "Ok rechargement !"
            messagebox.showinfo(title="Mise à jour infos", message=msg, parent=self, type=messagebox.OK)

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

    def tree_autosize(self):
        cols_to_autosize = (0,)  # uniquement première colonne
        min_size = 100

        tkfont = font.nametofont("TkTextFont")
        for col in cols_to_autosize:
            max_width = tkfont.measure(self.tree.heading(col)["text"] + "    ")
            for item in self.tree.get_children(""):
                item_width = tkfont.measure(self.tree.set(item, col) + "    ")
                max_width = max(max(max_width, item_width), min_size)

            self.tree.column(col, width=max_width)

    def tree_selection_change(self, _: Event):
        if self.new_server and not self.tree.focus() == str(id(self.server)):
            self.tree.delete(id(self.server))
            self.new_server = False

        selected_iid = self.tree.focus()
        selected_values = self.tree.item(selected_iid, "values")

        if selected_values == "":
            return

        for _, server in self.servers.servers_dict.items():
            if selected_iid == str(id(server)):
                self.server = server
                self.server_update()
                break

    def server_update(self):
        self.type_box.set(ServerType[self.server.type].value)

        for key in self.entries.keys():
            val = getattr(self.server, key)
            self.entries[key]["var"].set(val)

        self.groups_set()

    def toggle_password(self, w_caller: tk.Widget, w_target: tk.Widget, hide_char: str = "\U000025cf"):
        if w_target["show"] == "":
            w_caller.config(text="Voir")
            w_target.config(show=hide_char)
        else:
            w_caller.config(text="Masquer")
            w_target.config(show="")

    def groups_set(self):
        self.groups = self.servers.groups

        self.listbox.delete(0, tk.END)
        for group in sorted(self.groups):
            self.listbox.insert(tk.END, group)

        for i, line in enumerate(self.listbox.get(0, tk.END)):
            if line in self.server.grp_authorized:
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

    # ------------------------------------------------------------------------------------------
    # Autres traitements
    # ------------------------------------------------------------------------------------------
    def server_new(self):
        self.server = Server()
        iid = id(self.server)
        self.tree.insert("", tk.END, values=("", ""), iid=iid)

        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.server_update()
        self.new_server = True

    def server_save(self):
        server_id = self.entries["id"]["var"].get()

        # vérifications avant enregistrement
        if not server_id:
            msg = "L'identifiant est obligatoire !"
            messagebox.showerror(title="Mise à jour infos serveur", message=msg, parent=self, type=messagebox.OK)
            return
        if not self.type_box.get():
            msg = "Un type doit être choisi !"
            messagebox.showerror(title="Mise à jour infos serveur", message=msg, parent=self, type=messagebox.OK)
            return

        # mise à jour des infos du serveur
        self.server.type = ServerType(self.type_box.get()).name

        for key in self.entries.keys():
            val = self.entries[key]["var"].get()
            setattr(self.server, key, val)

        self.server.grp_authorized = [self.listbox.get(line) for line in self.listbox.curselection()]

        # enregistrement des infos
        try:
            result = self.server.save()
        except Exception as e:
            msg = "Erreur inattendue lors de l'enregistrement :\n"
            messagebox.showerror(
                title="Mise à jour infos serveur", message=msg + str(e), parent=self, type=messagebox.OK
            )
            return

        # signalement de la fin de l'enregistrement
        if result:
            self.new_server = False
            self.reload_all()
            msg = "Modifications enregistrées"
            messagebox.showinfo(title="Mise à jour infos serveur", message=msg, parent=self, type=messagebox.OK)
        else:
            msg = "Problème lors de l'enregistrement !"
            messagebox.showerror(title="Mise à jour infos serveur", message=msg, parent=self, type=messagebox.OK)

    def server_remove(self):
        curr_pos = self.tree.index(self.tree.focus())
        result = self.server.delete()
        if result:
            self.reload_all()
            self.tree_select_pos(max(curr_pos - 1, 0))
            msg = "Suppression du serveur réussi"
            messagebox.showinfo(title="Suppression infos serveur", message=msg, parent=self, type=messagebox.OK)
        else:
            msg = "Problème lors de la suppression !"
            messagebox.showerror(title="Suppression infos serveur", message=msg, parent=self, type=messagebox.OK)

    def import_servers(self):
        title = "Fichier à importer"
        types = (("Fichier csv", "*.csv"), ("Tous les fichiers", "*.*"))
        filename = filedialog.askopenfilename(title=title, filetypes=types, parent=self)

        result = self.servers.csv_import(filename)
        self.reload_all()
        if result:
            msg = "Fin de l'import des serveurs"
            messagebox.showinfo(title="Import", message=msg, parent=self, type=messagebox.OK)
        else:
            msg = "Quelque chose ne s'est pas bien passé lors de l'import !"
            messagebox.showerror(title="Import", message=msg, parent=self, type=messagebox.OK)

    def export_servers(self):
        title = "Fichier à exporter"
        types = (("Fichier csv", "*.csv"),)
        filename = filedialog.asksaveasfilename(title=title, filetypes=types, defaultextension=".csv", parent=self)
        if not filename:
            return

        result = self.servers.csv_export(filename, overwrite=True)
        if result:
            msg = "Fin de l'export des utilisateurs"
            messagebox.showinfo(title="Export", message=msg, parent=self, type=messagebox.OK)
        else:
            msg = "Quelque chose ne s'est pas bien passé lors de l'export !"
            messagebox.showerror(title="Export", message=msg, parent=self, type=messagebox.OK)

    def app_exit(self, _: Event = None):
        if self.parent:
            self.parent.wm_attributes("-disabled", False)

        self.destroy()

        if self.parent is None:
            self.quit()


if __name__ == "__main__":
    my_app = ServersWindow()
    my_app.mainloop()
