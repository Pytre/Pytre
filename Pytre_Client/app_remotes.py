import logging
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk, Event, messagebox, filedialog
from threading import Thread
from queue import Queue
from collections.abc import Iterator

from Pytre_Core import utils
from Pytre_Core.remotes import Remotes, Remote, RemoteAddress
from Pytre_Core.about import APP_NAME

import Pytre_Client.ui_utils as ui_utils
from Pytre_Client.ui_utils_thread import tk_call_when_ready
from Pytre_Client.app_theme import set_theme, set_menus, theme_is_on
from Pytre_Client.MsgOverlay import MsgOverlay
from Pytre_Client.ui_utils_csv import CsvTaskConfig, csv_process_async


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    utils.log(msg, level=level, sublogger=sublogger)


@dataclass(frozen=True)
class WidgetGroup:
    caption: str
    w_label: ttk.Label
    w_entry: ttk.Widget
    var: tk.Variable


class RemotesWindow(tk.Toplevel):
    @classmethod
    def show(cls, remotes: Remotes, parent=None) -> bool:
        """Create windows and reports if modifications has been made after it has been closed"""
        dialog = RemotesWindow(remotes, parent=parent)
        dialog.wait_window()
        return dialog.remotes_modified

    def __init__(self, remotes: Remotes, parent=None):
        super().__init__()
        self.parent = parent
        if self.parent:
            self.focus_set()
        else:
            assert isinstance(self.master, tk.Tk)
            self.master.withdraw()

        self.detached_items: set[str] = set()
        self.filter_var: tk.StringVar

        self.remotes: Remotes = remotes
        self.default_sort_col: str = "description"
        self.sort_info: dict[str, str | bool | None] = {"col": None, "reverse": None}
        self.remotes_modified: bool = False

        set_theme(self)
        self._setup_ui()
        self._events_binds()

        self.tree_refresh(notify_end=False)

    # ------------------------------------------------------------------------------------------
    # Création de l'interface
    # ------------------------------------------------------------------------------------------
    def _setup_ui(self):
        self.title(f"{APP_NAME} - Gestion des remotes")
        self.geometry("650x400")
        if self.parent:
            ui_utils.ui_center(self, self.parent)
        elif utils.get_system() == "Windows":
            self.geometry("+100+75")
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

        self.menu_remotes = tk.Menu(self.menubar, tearoff=False)
        self.menu_remotes.add_command(label="Ajouter...", command=self.remote_add)
        self.menu_remotes.add_command(label="Modifier...", command=self.remote_modify)
        self.menu_remotes.add_command(label="Supprimer...", command=self.remote_delete)
        self.menu_remotes.add_separator()
        self.menu_remotes.add_command(label="Importer...", command=self.import_remotes)
        self.menu_remotes.add_command(label="Exporter...", command=self.export_remotes)
        self.menubar.add_cascade(label="Remotes", menu=self.menu_remotes)

        if theme_is_on():
            menus: list[tk.Menu] = [self.menubar, self.menu_remotes]
            set_menus(menus)

    def _setup_ui_ctrl(self):
        self.btn_add = ttk.Button(self.ctrl_frame, text="Ajouter", command=self.remote_add)
        self.btn_modify = ttk.Button(self.ctrl_frame, text="Modifier", command=self.remote_modify)
        self.btn_delete = ttk.Button(self.ctrl_frame, text="Supprimer", command=self.remote_delete)

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

        self.tree = ttk.Treeview(self.tree_frame, show="headings", selectmode="extended")
        self.tree_cols_config()

        xbar = ttk.Scrollbar(self.tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(xscrollcommand=xbar.set)
        ybar = ttk.Scrollbar(self.tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=ybar.set)

        self.tree.grid(row=0, column=0, columnspan=1, padx=2, pady=2, sticky="nswe")
        xbar.grid(row=1, column=0, sticky="we")
        ybar.grid(row=0, column=1, sticky="ns")

    def _tree_cols(self) -> dict:
        cols = {
            "description": {"text": "Libellé", "minwidth": 175, "stretch": False},
            "url": {"text": "URL", "minwidth": 275, "stretch": True},
            "port": {"text": "Port", "minwidth": 50, "stretch": False},
            "username": {"text": "Login", "minwidth": 100, "stretch": False},
        }

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
        self.tree.bind("<Double-Button-1>", lambda e: self.remote_modify())
        self.tree.bind("<Button-3>", lambda e: self.menu_remotes.tk_popup(e.x_root, e.y_root))

        self.protocol("WM_DELETE_WINDOW", self.app_exit)  # arrêter le programme quand fermeture de la fenêtre

    # ------------------------------------------------------------------------------------------
    # Mise à jour interface
    # ------------------------------------------------------------------------------------------
    def tree_cols_config(self):
        cols = self._tree_cols()

        # mettre à jour la configuration des colonnes
        self.tree.configure(columns=list(cols.keys()))

        # configurer chaque colonne et son entête
        for col, attr in cols.items():
            self.tree.heading(col, text=attr["text"], command=lambda c=col: self.tree_header_click(c))
            self.tree.column(col, width=attr["minwidth"], minwidth=attr["minwidth"], stretch=attr["stretch"])

    def tree_filter(self, cols: list[str] = []):
        cols = cols if cols else list(self._tree_cols().keys())
        i = 0
        while i < len(cols):
            if cols[i] not in self.tree["columns"]:
                log(f'Error when filtering : "{cols[i]}" do not exists in tree columns', logging.WARNING)
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
                self.tree_sort(str(self.sort_info["col"]), bool(self.sort_info["reverse"]))
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
                sort_symbols = ("\U000025b3", "\U000025bd")
                title = f"{title} {sort_symbols[0]}" if not reverse else f"{title} {sort_symbols[1]}"
            self.tree.heading(col, text=title)

        # mise à jour info du dernier tri
        self.sort_info["col"] = sort_col
        self.sort_info["reverse"] = reverse

    def tree_header_click(self, col: str):
        reverse = False
        if col == self.sort_info["col"] and self.sort_info["reverse"] is False:
            reverse = True

        self.tree_sort(col, reverse)

    def tree_refresh(self, sort_col: str = "", notify_end: bool = True):
        sort_col = self.default_sort_col if sort_col == "" else sort_col

        overlay = MsgOverlay.display(self, "Chargement des remotes...", 1500)
        self.lock_ui()

        def worker():
            remotes: dict[str, Remote] = {}
            try:
                remotes = self.remotes.get_all_remotes()
            finally:
                # envoi message de fin pour mettre à jour l'UI et finaliser dans le thread principal
                result_queue.put(remotes)

        def refresh_end(remotes: dict[str, Remote]):
            cols = self._tree_cols()

            self.tree.delete(*self.tree.get_children())
            self.tree.delete(*self.detached_items)
            self.detached_items = set()

            for uuid, remote in remotes.items():
                values: list = []
                for col in cols.keys():
                    try:
                        value = getattr(remote.address, col)
                        values.append(value)
                    except AttributeError:
                        log(f"Error trying to retrieve inexistant attributes {col} for remotes object", logging.ERROR)

                try:
                    self.tree.insert("", tk.END, iid=uuid, values=values)
                except Exception as e:
                    messagebox.showerror("Erreur chargement liste", str(e))

            self.tree_sort(sort_col)
            self.tree_filter()

            overlay.hide(callback=self.unlock_ui)

            if notify_end:
                messagebox.showinfo("Rechargement", "Ok liste des remotes rechargées", parent=self)

        result_queue = Queue()
        Thread(target=worker, daemon=True).start()
        tk_call_when_ready(self, result_queue, refresh_end)

    def tree_select_pos(self, pos: int, e: Event | None = None):
        if e is not None and e.widget is self.filter:  # désactivation si le filtre est en saisi
            return

        ui_utils.tree_select_pos(self.tree, pos)

    def tree_select_move(self, offset: int, e: Event | None = None):
        if e is not None and e.widget is self.filter:  # désactivation si le filtre est en saisi
            return

        ui_utils.tree_select_move(self.tree, offset)

    def app_exit(self, _: Event | None = None):
        self.destroy()
        if self.parent is None:
            self.quit()

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
        frames_lst = self.ctrl_frame.winfo_children() + self.tree_frame.winfo_children()
        for widget in frames_lst:
            if type(widget) in [ttk.Button, ttk.Entry]:
                widget["state"] = "disable"
            elif widget in [self.tree]:
                self.tree["selectmode"] = "none"

    def unlock_ui(self):
        # enable all menus
        menu: tk.Menu
        for menu, index in self.menu_commands(self.menubar):
            menu.entryconfig(index, state="normal")

        # enable all widgets
        frames_lst = self.ctrl_frame.winfo_children() + self.tree_frame.winfo_children()
        for widget in frames_lst:
            if type(widget) in [ttk.Button, ttk.Entry]:
                widget["state"] = "enable"
            elif widget in [self.tree]:
                self.tree["selectmode"] = "extended"

    # ------------------------------------------------------------------------------------------
    # Autres traitements
    # ------------------------------------------------------------------------------------------
    def remote_add(self):
        RemoteDialog(self)

    def remote_modify(self):
        items = self.tree.selection()
        if items:
            remote: Remote | None = self.remotes.find_remote_from_uuid(items[0])
            if remote:
                RemoteDialog(self, remote)

    def remote_delete(self):
        items = self.tree.selection()

        if not self._delete_confirm(items):
            return

        nb_ok, errors = 0, []
        for item in items:
            try:
                remote: Remote | None = self.remotes.find_remote_from_uuid(items[0])
                if remote:
                    remote.delete()
                    self.remotes_modified = True
                self.tree.delete(item)
                nb_ok += 1
            except LookupError:
                errors.append(item)

        self._delete_after_msg(nb_ok, errors)

    def _delete_confirm(self, items: tuple[str, ...]) -> bool:
        if len(items) == 0:
            return False
        elif len(items) == 1:
            item_vals = self.tree.item(items[0], "values")
            url = item_vals[list(self._tree_cols()).index("url")]
            port = item_vals[list(self._tree_cols()).index("port")]
            username = item_vals[list(self._tree_cols()).index("username")]
            msg = f"Confirmation de la suppression de :\n{url};{port}\navec login : {username}"
        else:
            msg = f"Confirmation de la suppression de {len(items)} remotes"

        if not messagebox.askokcancel(title="Suppression", message=msg, parent=self, icon=messagebox.WARNING):
            return False

        return True

    def _delete_after_msg(self, nb_ok: int, errors: list[str]):
        if nb_ok == 0:
            msg = "Aucune remote n'a pu être supprimée"
        elif nb_ok == 1:
            msg = "1 remote a été supprimée"
        else:
            msg = f"{nb_ok} remotes ont été supprimées"

        if len(errors) == 1 and nb_ok:
            msg = msg + f" et {len(errors)} n'a pas pu l'être :\n{errors[0]}"
        elif len(errors) > 1 and nb_ok:
            msg = msg + f" et {len(errors)} n'ont pas pas pu l'être :\n- " + "\n- ".join(errors)

        messagebox.showinfo(title="Suppression", message=msg, parent=self)

    def import_remotes(self):
        title = "Fichier à importer"
        types = (("Fichier csv", "*.csv"), ("Tous les fichiers", "*.*"))
        filename = filedialog.askopenfilename(title=title, filetypes=types, parent=self)
        if not filename:
            return

        def import_end(result: bool) -> None:
            if result:
                self.remotes_modified = True
            self.tree_refresh()

        csv_task_cfg = CsvTaskConfig(
            ui_parent=self,
            ui_lock_fn=self.lock_ui,
            ui_unlock_fn=self.unlock_ui,
            overlay_dialog_msg="Import des remotes...",
            overlay_dialog_wait_ms=1500,
            csv_process_fn=lambda: self.remotes.csv_import(filename),
            end_msgbox_title="Import",
            end_msgbox_msg="Fin de l'import des remotes",
            end_fn=import_end,
        )

        csv_process_async(csv_task_cfg)

    def export_remotes(self):
        title = "Fichier à exporter"

        msg = "Voulez-vous que les mots de passe soit encryptés ?"
        encrypt_pwd = messagebox.askyesno(title=title, message=msg, parent=self)

        types = (("Fichier csv", "*.csv"),)
        filename = filedialog.asksaveasfilename(title=title, filetypes=types, defaultextension=".csv", parent=self)
        if not filename:
            return

        csv_task_cfg = CsvTaskConfig(
            ui_parent=self,
            ui_lock_fn=self.lock_ui,
            ui_unlock_fn=self.unlock_ui,
            overlay_dialog_msg="Export des remotes...",
            overlay_dialog_wait_ms=1500,
            csv_process_fn=lambda: self.remotes.csv_export(filename, overwrite=True, encrypt=encrypt_pwd),
            end_msgbox_title="Export",
            end_msgbox_msg="Fin de l'export des remotes",
            end_fn=None,
        )

        csv_process_async(csv_task_cfg)


class RemoteDialog(tk.Toplevel):
    def __init__(self, parent: RemotesWindow, address: Remote | None = None):
        super().__init__(master=parent)
        self.parent: RemotesWindow = parent

        self.remotes: Remotes = self.parent.remotes
        self.remote: Remote | None = address
        self.remote_modified: bool = False

        self.focus_set()
        if utils.get_system() == "Linux":
            self.update_idletasks()  # needed to update correctly on Linux
        ui_utils.ui_disable_parent(self, self.parent)
        self.transient(self.parent)

        set_theme(self)
        self._setup_ui()
        self._events_binds()

        if self.remote:
            self.set_entries()

    # ------------------------------------------------------------------------------------------
    # Création de l'interface
    # ------------------------------------------------------------------------------------------
    def _setup_ui(self):
        if not self.remote:
            self.title(f"{APP_NAME} - Ajout remote")
        else:
            self.title(f"{APP_NAME} - Modification remote")

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
        self._setup_buttons()

    def _setup_entries(self):
        self.entries_frame.columnconfigure(1, weight=1)

        entries_caption: dict[str, str] = {
            "url": "URL",
            "port": "Port",
            "username": "Login",
            "password": "Mot de passe",
            "description": "Description",
        }
        self.entries: dict[str, WidgetGroup] = {}

        num_row = 0
        for key, caption in entries_caption.items():
            my_label = ttk.Label(self.entries_frame, text=caption + " : ")
            my_tk_var = tk.IntVar() if key == "port" else tk.StringVar()
            my_entry = ttk.Entry(self.entries_frame, textvariable=my_tk_var)

            self.entries[key] = WidgetGroup(caption=caption, w_label=my_label, w_entry=my_entry, var=my_tk_var)

            col_span = 2
            if key == "password":
                col_span = 1
                reveal_button = ttk.Button(self.entries_frame, width=8)
                reveal_button.config(command=lambda wc=reveal_button, wt=my_entry: self.toggle_password(wc, wt))
                self.toggle_password(reveal_button, my_entry)
                reveal_button.grid(row=num_row, column=2, padx=2, pady=2, sticky="nswe")

            my_entry.grid(row=num_row, column=1, columnspan=col_span, padx=2, pady=2, sticky="nswe")
            my_label.grid(row=num_row, column=0, padx=2, pady=2, sticky="nswe")
            num_row += 1

    def _setup_buttons(self):
        self.buttons_frame.columnconfigure(0, weight=1)

        self.btn_save = ttk.Button(self.buttons_frame, text="Enregistrer", command=self.remote_save)
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
        if self.remote is None:
            raise RuntimeError("No remote selected !")

        for key, w_group in self.entries.items():
            val = getattr(self.remote.address, key, "")
            w_group.var.set(val)

    def toggle_password(self, w_caller: ttk.Button, w_target: ttk.Entry, hide_char: str = "\U000025cf"):
        ui_utils.toggle_password(w_caller, w_target, hide_char)

    def remote_save(self):
        if self.remote:
            remote = self.remote
        else:
            remote: Remote = Remote(self.remotes, RemoteAddress("", 8000, ""))

        overlay = MsgOverlay.display(self, "Enregistrement en cours...", 0)

        # mise à jour des infos de la remote
        for key, w_group in self.entries.items():
            val = w_group.var.get()
            setattr(remote.address, key, val)

        # enregistrement des infos
        def worker():
            msg: str = ""
            try:
                remote.save()
            except Exception as e:
                msg = "Erreur inattendue lors de l'enregistrement :\n" + str(e)
            finally:
                result_queue.put(msg)

        def save_end(error_msg: str = ""):
            overlay.hide()

            if error_msg:
                messagebox.showerror(title="Erreur Enregistrement", message=error_msg, parent=self)
            else:
                self.remote_modified = True
                self.close()

        result_queue = Queue()
        Thread(target=worker, daemon=True).start()
        tk_call_when_ready(self, result_queue, save_end)

    def close(self, _: Event | None = None):
        ui_utils.ui_undisable_parent(self, self.parent)

        if self.remote_modified:
            self.parent.remotes_modified = True
            self.parent.tree_refresh(notify_end=False)
        self.parent.focus_set()

        self.destroy()


if __name__ == "__main__":
    remotes = Remotes()
    my_app = RemotesWindow(remotes)
    my_app.mainloop()
