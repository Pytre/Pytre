import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk, Event, filedialog, messagebox
from collections.abc import Iterator
from typing import cast

from Pytre_Core import utils
from Pytre_Core.kee import Kee
from Pytre_Core.servers import Servers, Server, ServerType
from Pytre_Core.about import APP_NAME

import Pytre_Client.ui_utils as ui_utils
from Pytre_Client.app_theme import set_theme, set_menus, ThemeColors, theme_is_on
from Pytre_Client.InputDialog import InputDialog
from Pytre_Client.ui_utils_csv import CsvTaskConfig, csv_process_async


@dataclass(frozen=True)
class WidgetGroup:
    caption: str
    w_label: ttk.Label
    w_entry: ttk.Widget
    var: tk.Variable


class ServersWindow(tk.Toplevel):
    @classmethod
    def show(cls, servers: Servers, parent=None) -> bool:
        """Create windows and reports if modifications has been made after it has been closed"""
        dialog = ServersWindow(servers, parent=parent)
        dialog.wait_window()
        return dialog.servers_modified

    def __init__(self, servers: Servers, parent=None):
        super().__init__()
        self.parent = parent
        if self.parent:
            self.focus_set()
            ui_utils.ui_disable_parent(self, self.parent)
            self.transient(self.parent)
        else:
            assert isinstance(self.master, tk.Tk)
            self.master.withdraw()

        self.servers = servers
        self.servers.get_all_servers()
        self.type_server: tuple[str, ...] = tuple([type.value for type in ServerType if not type.server_only])
        self.groups: set[str] = set()

        self.server: Server | None = None
        self.new_server: bool = False
        self.servers_modified: bool = False

        set_theme(self)
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
        elif utils.get_system() == "Windows":
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

        if theme_is_on():
            menus: list[tk.Menu] = [self.menubar, menu_servers]
            set_menus(menus)

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
        self.tree.configure(xscrollcommand=xbar.set)
        ybar = ttk.Scrollbar(self.tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=ybar.set)

        self.tree.grid(row=0, column=0, columnspan=1, padx=2, pady=2, sticky="nswe")
        xbar.grid(row=1, column=0, sticky="we")
        ybar.grid(row=0, column=1, sticky="ns")

        self.tree_frame.rowconfigure(0, weight=1)
        self.tree_frame.columnconfigure(0, weight=1)

    def _setup_type(self):
        my_label = ttk.Label(self.entries_frame, text="Type : ")
        self.type_box = ttk.Combobox(self.entries_frame, state="readonly", values=self.type_server)
        if len(self.type_server) <= 1:
            self.type_box["state"] = "disable"

        my_label.grid(row=0, column=0, padx=2, pady=2, sticky="nswe")
        self.type_box.grid(row=0, column=1, columnspan=2, padx=2, pady=2, sticky="nswe")

    def _setup_entries(self):
        entries_caption: dict[str, str] = {
            "id": "Id",
            "description": "Libellé",
            "user": "Login",
            "password": "Mot de passe",
            "charset": "Charset",
            "database": "Database",
            "host": "Host",
            "port": "Port",
            "server": "Server",
            "login_timeout": "Login Timeout",
            "timeout": "Timeout",
        }

        self.entries: dict[str, WidgetGroup] = {}
        num_row = 1
        for key, caption in entries_caption.items():
            my_label = ttk.Label(self.entries_frame, text=caption + " : ")
            my_tk_var = tk.StringVar()
            my_entry = ttk.Entry(self.entries_frame, textvariable=my_tk_var)

            self.entries[key] = WidgetGroup(caption=caption, w_label=my_label, w_entry=my_entry, var=my_tk_var)

            my_label.grid(row=num_row, column=0, padx=2, pady=2, sticky="nswe")

            if key == "password" and isinstance(my_entry, ttk.Entry):
                my_entry.configure(width=40)
                # create a toggle show password button
                reveal_button = ttk.Button(self.entries_frame, width=8)
                reveal_button.config(command=lambda wc=reveal_button, wt=my_entry: self.toggle_password(wc, wt))
                self.toggle_password(reveal_button, my_entry)
                # grid entry and reveal button on the same row
                my_entry.grid(row=num_row, column=1, columnspan=1, padx=2, pady=2, sticky="nswe")
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
        if theme_is_on():
            self.listbox.configure(selectbackground=ThemeColors.accent, selectforeground=ThemeColors.text_secondary)
        ybar = ttk.Scrollbar(self.groups_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=ybar.set)

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
                item_to_select = str(id(server))

        self.tree_autosize()
        if item_to_select:
            self.tree.selection_set(item_to_select)
            self.tree.focus(item_to_select)
            self.tree.see(item_to_select)
        else:
            self.tree_select_pos(0)

        if notify_end:
            msg = "Ok rechargement !"
            messagebox.showinfo(title="Mise à jour infos", message=msg, parent=self)

    def tree_select_move(self, offset: int):
        ui_utils.tree_select_move(self.tree, offset)

    def tree_select_pos(self, pos: int):
        ui_utils.tree_select_pos(self.tree, pos)

    def tree_autosize(self):
        cols_to_autosize = (0,)  # uniquement première colonne
        ui_utils.tree_autosize(self.tree, columns=cols_to_autosize, min_size=100)

    def tree_selection_change(self, _: Event):
        if self.new_server and not self.tree.focus() == str(id(self.server)):
            self.tree.delete(id(self.server))
            self.new_server = False

        selected_iid = self.tree.focus()
        selected_values = self.tree.item(selected_iid, "values")

        if selected_values == "":
            return

        for server in self.servers.servers_dict.values():
            if selected_iid == str(id(server)):
                self.server = server
                self.server_update()
                break

    def server_update(self):
        if self.server is None:
            raise RuntimeError("No server selected !")

        self.type_box.set(ServerType[self.server.type].value)

        for key, w_group in self.entries.items():
            val = getattr(self.server, key)
            w_group.var.set(val)

        self.groups_set()

    def toggle_password(self, w_caller: ttk.Button, w_target: ttk.Entry, hide_char: str = "\U000025cf"):
        ui_utils.toggle_password(w_caller, w_target, hide_char)

    def groups_set(self):
        if self.server is None:
            raise RuntimeError("No server selected !")

        self.groups = self.servers.groups

        self.listbox.delete(0, tk.END)
        for group in sorted(self.groups):
            self.listbox.insert(tk.END, group)

        for i, line in enumerate(self.listbox.get(0, tk.END)):
            if line in self.server.grp_authorized:
                self.listbox.selection_set(i)

    def group_add(self):
        curr_groups: list[str] = self.listbox.get(0, tk.END)
        new_group = InputDialog.ask("Nouveau groupe", "Groupe à ajouter :", parent=self)

        if not new_group or any(new_group.lower() == group.lower() for group in curr_groups):
            return

        pos = 0
        for line in curr_groups:
            if new_group > line:
                pos += 1
            else:
                break

        self.listbox.insert(pos, new_group)
        self.listbox.selection_set(pos)

    # ------------------------------------------------------------------------------------------
    # Verrouillage / Déverrouillage de l'UI
    # ------------------------------------------------------------------------------------------
    def menu_commands(self, menu: tk.Menu) -> Iterator[tuple[tk.Menu, int]]:
        end_index = menu.index("end")
        if end_index is None:
            return

        for i in range(end_index + 1):
            try:
                entry_type = menu.type(i)
            except tk.TclError:
                continue

            if entry_type == "command":
                yield (menu, i)
            elif entry_type == "cascade":
                submenu_name = menu.entrycget(i, "menu")
                if submenu_name:
                    submenu = menu.nametowidget(submenu_name)
                    yield from self.menu_commands(submenu)

    def lock_ui(self):
        # disable all menus
        menu: tk.Menu
        for menu, index in self.menu_commands(self.menubar):
            menu.entryconfig(index, state="disable")

        # disable all widgets
        frames_lst = (self.tree_frame, self.buttons_frame, self.entries_frame, self.groups_frame)
        for frame in frames_lst:
            for widget in frame.winfo_children():
                if type(widget) in [ttk.Button, ttk.Entry]:
                    widget["state"] = "disable"
                elif widget in [self.type_box, self.listbox]:
                    widget["state"] = "disable"
                elif widget in [self.tree]:
                    self.tree["selectmode"] = "none"

    def unlock_ui(self):
        # enable all menus
        menu: tk.Menu
        for menu, index in self.menu_commands(self.menubar):
            menu.entryconfig(index, state="normal")

        # enable all widgets
        frames_lst = (self.tree_frame, self.buttons_frame, self.entries_frame, self.groups_frame)
        for frame in frames_lst:
            for widget in frame.winfo_children():
                if type(widget) in [ttk.Button, ttk.Entry]:
                    widget["state"] = "enable"
                elif widget in [self.type_box]:
                    widget["state"] = "readonly"
                elif widget in [self.tree]:
                    widget["selectmode"] = "extended"
                elif widget in [self.listbox]:
                    widget["state"] = "normal"

    # ------------------------------------------------------------------------------------------
    # Autres traitements
    # ------------------------------------------------------------------------------------------
    def server_new(self):
        self.server = Server(self.servers)
        iid = id(self.server)
        self.tree.insert("", tk.END, values=("", ""), iid=iid)

        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.server_update()
        self.new_server = True

    def server_save(self):
        if self.server is None:
            raise RuntimeError("No server selected !")

        server_id = self.entries["id"].var.get()

        # vérifications avant enregistrement
        if not server_id:
            msg = "L'identifiant est obligatoire !"
            messagebox.showerror(title="Mise à jour infos serveur", message=msg, parent=self)
            return
        if not self.type_box.get():
            msg = "Un type doit être choisi !"
            messagebox.showerror(title="Mise à jour infos serveur", message=msg, parent=self)
            return

        # mise à jour des infos du serveur
        self.server.type = ServerType(self.type_box.get()).name

        for key in self.entries.keys():
            val = self.entries[key].var.get()
            setattr(self.server, key, val)

        self.server.grp_authorized = [self.listbox.get(line) for line in self.listbox.curselection()]

        # enregistrement des infos
        try:
            result = self.server.save()
        except Exception as e:
            msg = "Erreur inattendue lors de l'enregistrement :\n"
            messagebox.showerror(title="Mise à jour infos serveur", message=msg + str(e), parent=self)
            return

        # signalement de la fin de l'enregistrement
        if result:
            self.new_server = False
            self.servers_modified = True
            self.reload_all()
            msg = "Modifications enregistrées"
            messagebox.showinfo(title="Mise à jour infos serveur", message=msg, parent=self)
        else:
            msg = "Problème lors de l'enregistrement !"
            messagebox.showerror(title="Mise à jour infos serveur", message=msg, parent=self)

    def server_remove(self):
        if self.server is None:
            raise RuntimeError("No server selected !")

        curr_pos = self.tree.index(self.tree.focus())
        result = self.server.delete()
        if result:
            self.reload_all()
            self.tree_select_pos(max(curr_pos - 1, 0))
            self.servers_modified = True
            msg = "Suppression du serveur réussi"
            messagebox.showinfo(title="Suppression infos serveur", message=msg, parent=self)
        else:
            msg = "Problème lors de la suppression !"
            messagebox.showerror(title="Suppression infos serveur", message=msg, parent=self)

    def import_servers(self):
        title = "Fichier à importer"
        types = (("Fichier csv", "*.csv"), ("Tous les fichiers", "*.*"))
        filename = filedialog.askopenfilename(title=title, filetypes=types, parent=self)
        if not filename:
            return

        def import_end(result: bool) -> None:
            if result:
                self.servers_modified = True
            self.reload_all()

        csv_task_cfg = CsvTaskConfig(
            ui_parent=self,
            ui_lock_fn=self.lock_ui,
            ui_unlock_fn=self.unlock_ui,
            overlay_dialog_msg="Import des serveurs...",
            overlay_dialog_wait_ms=1500,
            csv_process_fn=lambda: self.servers.csv_import(filename),
            end_msgbox_title="Import",
            end_msgbox_msg="Fin de l'import des serveurs",
            end_fn=import_end,
        )

        csv_process_async(csv_task_cfg)

    def export_servers(self):
        title = "Fichier à exporter"
        types = (("Fichier csv", "*.csv"),)
        filename = filedialog.asksaveasfilename(title=title, filetypes=types, defaultextension=".csv", parent=self)
        if not filename:
            return

        csv_task_cfg = CsvTaskConfig(
            ui_parent=self,
            ui_lock_fn=self.lock_ui,
            ui_unlock_fn=self.unlock_ui,
            overlay_dialog_msg="Export des serveurs...",
            overlay_dialog_wait_ms=1500,
            csv_process_fn=lambda: self.servers.csv_export(filename, overwrite=True),
            end_msgbox_title="Export",
            end_msgbox_msg="Fin de l'export des serveurs",
            end_fn=None,
        )

        csv_process_async(csv_task_cfg)

    def app_exit(self, _: Event | None = None):
        if self.parent:
            ui_utils.ui_undisable_parent(self, self.parent)

        self.destroy()

        if self.parent is None:
            self.quit()


if __name__ == "__main__":
    kee = Kee()
    servers = Servers(kee)
    my_app = ServersWindow(servers)
    my_app.mainloop()
