import sys
import os
import getpass

import csv
import json
from pathlib import Path
from tkinter import messagebox

from pykeepass import PyKeePass
from pykeepass.exceptions import CredentialsError
from pykeepass.entry import Entry
from pykeepass.group import Group

from ui.InputDialog import InputDialog
from credentials import crypted_file_pwd_get, crypted_file_pwd_history, crypted_file_pwd_change


KEE_FILE = Path().cwd() / "Pytre.db"
KEE_PWD = crypted_file_pwd_get()
BLANK_FILE = "res/blank.db"  # relative path for blank db
BLANK_PWD = "password"  # password for blank db


def get_app_path() -> Path:
    # If app is run as a bundle then PyInstaller bootloader
    # extends sys with a flag frozen = True and sets app path into _MEIPASS

    if getattr(sys, "frozen", False):
        application_path = sys._MEIPASS
    else:
        application_path = os.path.dirname(os.path.abspath(__file__))

    return Path(application_path)


class Kee:
    _instance = None
    file: str = KEE_FILE
    pwd: str = ""
    db: PyKeePass = None
    is_open: bool = False
    is_ko: bool = False
    opening_count: int = 0

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not Kee.pwd:
            self.pwd = self.pwd_get()
        if not self.is_open and not self.is_ko:
            self._open_db()

    def _open_db(self, reload: bool = False):
        """A utiliser uniquement par la méthode open_db des classes User, Server et Settings"""
        if not self.is_open:
            try:
                self.db = PyKeePass(self.file, password=self.pwd)
            except FileNotFoundError:
                self.create_db()
            except CredentialsError:  # si erreur mot de passe, tentative avec les précédents
                self.pwd_try_old_ones()

            self.is_open = True
            self.opening_count += 1
            print(f"Opening count : {self.opening_count}")
        elif reload:
            self.db.reload()
            self.opening_count += 1
            print(f"Opening count : {self.opening_count}")

    def save_db(self):
        try:
            self.db.save()
        except PermissionError:
            msg = (
                "Vous n'avez les droits d'accès à la base des paramètres !\n"
                + "Vos modifications n'ont pas pu être enregistées.\n\n"
                + "Merci d'alerter les administateurs."
            )
            messagebox.showerror(title="Erreur enregistrement", message=msg)

    def create_db(self):
        self.is_ko = True

        msg = "La base des paramètres n'a pas été trouvée !\n" + "Une nouvelle base va être créée."
        messagebox.showwarning(title="Base introuvable", message=msg)

        pwd = InputDialog.ask("Mot de passe, accès paramètres ?", "Mot de passe :")
        pwd = pwd if pwd else BLANK_PWD

        self.db = PyKeePass(get_app_path() / BLANK_FILE, password=BLANK_PWD)
        self.db.filename = self.file
        self.pwd_change(pwd, True)

        self._create_db_set_default()

        msg = "Une base des paramètres par défaut a été créée.\nMettez les à jour à l'aide du menu admin."
        messagebox.showinfo(title="Base créée", message=msg)

        self.is_ko = False

    def _create_db_set_default(self):
        self.db.root_group.name = "settings"

        grps = {"Paramètres": None, "Serveurs": None, "Utilisateurs": None}
        for grp in grps.keys():
            grps[grp] = self.db.add_group(self.db.root_group, grp)

        queries_folder = InputDialog.ask("Dossier des requêtes ?", "Dossier des requêtes :")
        queries_folder = queries_folder if queries_folder else "."

        params = {
            "DATE_FORMAT": "%d/%m/%Y",
            "DECIMAL_SEPARATOR": ",",
            "FIELD_SEPARATOR": ";",
            "QUERIES_FOLDER": queries_folder,
            "SETTINGS_VERSION": "2",
        }
        for k, v in params.items():
            self.db.add_entry(grps["Paramètres"], title=k, username=v, password="")

        server_cfg = ["charset", "database", "host", "login_timeout", "port", "server", "timeout"]
        s_entry: Entry = self.db.add_entry(grps["Serveurs"], title="Default", username="", password="")
        for item in server_cfg:
            s_entry.set_custom_property(item, "")

        self.save_db()

    def access_is_ko(self) -> bool:
        return self.is_ko

    def pwd_try_old_ones(self, pwds_list: list[str] = [], _iter: int = 0) -> bool:
        if self.is_ko:
            return  # une fois que l'accès est ko, plus d'essai

        if not pwds_list:
            pwds_list = crypted_file_pwd_history()[1:]

        for pwd in pwds_list:
            try:
                self.db = PyKeePass(self.file, password=pwd)
                self.pwd_change(pwd, True)
                self.is_ko = False
                msg = (
                    "Le mot de passe d'accès aux paramètres n'était pas valide !\n"
                    + "L'accès a pu être possible à l'aide d'un ancien mot de passe.\n\n"
                    + "Merci d'alerter les administateurs, surtout si vous avez eu "
                    + "une erreur d'enregistrement lorsque le programme à tenter de "
                    + "le mettre à jour."
                )
                messagebox.showwarning(title="Récupération mot de passe", message=msg)
                return True
            except CredentialsError:
                pass

        # si aucun des anciens mots de passe ne marche alors on demande à l'utilisateur le bon mot de passe
        pwd = InputDialog.ask("Mot de passe, accès paramètres ?", "Mot de passe :")
        if pwd and self.pwd_try_old_ones([pwd], _iter + 1):
            self.is_ko = False
            return True
        # si l'utilisateur n'en a pas été capable alors on affiche une erreur
        elif _iter == 0:
            self.is_ko = True
            msg = (
                "Le mot de passe d'accès aux paramètres n'était pas valide !\n"
                + "L'accès même avec les anciens mots de passe ne fonctionne pas.\n\n"
                + "Merci d'alerter les administateurs."
            )
            messagebox.showerror(title="Récupération mot de passe", message=msg)
            return False
        else:
            return False

    def pwd_get(self) -> str:
        pwd = crypted_file_pwd_get()
        return pwd

    def pwd_history(self) -> list[str]:
        history = crypted_file_pwd_history()
        return history

    def pwd_change(self, new_pwd: str, force_save: bool = False):
        if force_save or not new_pwd == self.pwd:
            self.pwd = new_pwd
            self.db.password = self.pwd
            self.save_db()
            crypted_file_pwd_change(new_pwd)


class User:
    kee: Kee = Kee()
    kee_grp_name: str = "Utilisateurs"
    kee_grp: Group = None

    @classmethod
    def open_db(cls, reload: bool = False) -> bool:
        cls.kee._open_db(reload)
        if cls.kee.is_ko:
            return False
        cls.kee_grp = cls.kee.db.find_groups(name=cls.kee_grp_name, first=True)
        return True

    @classmethod
    def users_admin_exists(cls, reload: bool = False) -> bool:
        cls.open_db(reload)

        admin_exists = False

        u_entry: Entry | None = None
        for u_entry in cls.kee.db.find_entries(username=r".*", group=cls.kee_grp, regex=True):
            if u_entry.get_custom_property("superuser") == "true":
                admin_exists = True
                break

        return admin_exists

    @classmethod
    def users_get_all(cls) -> list:
        cls.open_db(True)

        u_entry: Entry | None = None
        u_list: list[User] = []

        for u_entry in cls.kee.db.find_entries(username=r".*", group=cls.kee_grp, regex=True):
            u = User(entry=u_entry)
            u_list.append(u)

        return u_list

    @classmethod
    def users_add_groups(cls, usernames: list[str], groups: list[str]):
        cls.open_db()

        save: bool = False

        groups = [group for group in groups if not group == "all"]
        u_entry: Entry | None = None
        for u_entry in cls.kee.db.find_entries(username=r".*", group=cls.kee_grp, regex=True):
            if u_entry.username not in usernames:
                continue

            new_groups = u_entry.tags if u_entry.tags else []
            new_groups = list(set(new_groups + groups))

            if not new_groups == u_entry.tags:
                u_entry.tags = new_groups
                save = True

        if save:
            cls.kee.save_db()

    @classmethod
    def users_remove_groups(cls, usernames: list[str], groups: list[str]):
        cls.open_db()

        save: bool = False

        u_entry: Entry | None = None
        for u_entry in cls.kee.db.find_entries(username=r".*", group=cls.kee_grp, regex=True):
            if u_entry.username not in usernames or not u_entry.tags:
                continue

            new_groups = u_entry.tags
            for tag in u_entry.tags:
                if tag in groups:
                    new_groups.remove(tag)

            if not new_groups == u_entry.tags:
                u_entry.tags = new_groups
                save = True

        if save:
            cls.kee.save_db()

    @classmethod
    def csv_import(cls, filename: Path, delimiter: str = ";") -> bool:
        cls.open_db(True)

        entries_dict = {}
        u_entry: Entry
        for u_entry in cls.kee.db.find_entries(username=r".*", group=cls.kee_grp, regex=True):
            entries_dict[u_entry.username] = u_entry.upper()

        with open(filename, mode="r", encoding="latin-1") as csv_file:
            csv_reader = csv.reader(csv_file, delimiter=delimiter, quotechar='"')

            for i, row in enumerate(csv_reader):
                if i == 0:
                    continue

                # mapping des colonnes
                col_dict = {"username": 0, "title": 1, "superuser": 2, "grp_authorized": 3, "x3_id": 4, "msg_login": 5}
                row_dict = {}
                for key, val in col_dict.items():
                    row_dict[key] = row[val]

                # contrôle si utilisateurs à modifier ou à créer
                if row_dict["username"].upper() in entries_dict.keys():
                    u_entry = entries_dict[row_dict["username"]]
                else:
                    u_entry = cls.kee.db.add_entry(cls.kee_grp, "", row_dict["username"], "")

                # modification des entry de la base
                u_entry.title = row_dict["title"]

                u_entry.set_custom_property("x3_id", row_dict["x3_id"])
                u_entry.set_custom_property("msg_login", row_dict["msg_login"])

                if row_dict["superuser"] == "true":
                    u_entry.set_custom_property("superuser", "true")
                else:
                    u_entry.set_custom_property("superuser", "false")

                groups = row_dict["grp_authorized"].split(delimiter)
                u_entry.tags = [group for group in groups if not group == "all"]

            # une fois que toutes les entries sont à jour, sauvegarde de la base
            cls.kee.save_db()

            return True

    @classmethod
    def csv_export(cls, filename: Path, delimiter: str = ";", overwrite: bool = False) -> bool:
        if Path(filename).exists() and not overwrite:
            return False

        rows = [["Id", "Libellé", "Admin", "Groupes", "Id X3", "Login Message"]]
        cls.open_db(True)
        u_entry: Entry | None
        for u_entry in cls.kee.db.find_entries(username=r".*", group=cls.kee_grp, regex=True):
            row = [u_entry.username, u_entry.title]

            tags = u_entry.tags if u_entry.tags else []
            group = delimiter.join(tags)

            items = ["superuser", "group", "x3_id", "msg_login"]
            for item in items:
                if item == "group":
                    value = group
                else:
                    value = u_entry.get_custom_property(item)

                value = value if value is not None else ""
                row.append(value)

            rows.append(row)

        with open(filename, mode="w", encoding="latin-1", newline="") as csv_file:
            csv_writer = csv.writer(csv_file, delimiter=delimiter, quotechar='"', quoting=csv.QUOTE_MINIMAL)
            csv_writer.writerows(rows)

        return True

    @classmethod
    def get_username(cls) -> str:
        domain = os.environ.get("userdnsdomain") or ""
        name = getpass.getuser()
        domain_and_name = f"{domain}\\{name}" if domain else name
        return domain_and_name

    @classmethod
    def find_user_entry(cls, username) -> Entry | None:
        u_entries: list[Entry] = cls.kee.db.find_entries(username=".*", group=cls.kee_grp, regex=True)
        u_entries = [user for user in u_entries if username.upper() == user.username.upper()]
        if u_entries:
            return u_entries[0]
        else:
            return None

    def __init__(self, username: str = "", entry: Entry = None, detect_user: bool = True):
        """
        si pas de username et d'entry, et que detect_user est faux
        alors pas de chrgmt à partir de la base keepass
        """

        self.username: str = ""
        self.exists: bool = False
        self.is_authorized: bool = False
        self.title: str = ""
        self.x3_id: str = ""
        self.admin: bool = False
        self.grp_authorized: list[str] = ["all"]  # par défaut un utilisateur appartient au groupe all
        self.msg_login_cust: str = ""
        self.msg_login: str = ""

        if entry:
            self._load_from_entry(entry)
        elif username or detect_user:
            self.username = username if username else self.get_username()
            self.load()

        if not self.users_admin_exists():
            msg = (
                "Aucun administrateur n'existe !\n"
                + "Vous allez être ajouté en tant qu'administrateur par défaut.\n"
                + "Pensez à mettre à jour les utilisateurs après."
            )
            messagebox.showinfo(title="Base créée", message=msg)
            self.admin = True
            self.save()

    def to_dict(self) -> dict:
        return {
            "id": self.username,
            "exist_in_settings": self.exists,
            "is_authorized": self.is_authorized,
            "title": self.title,
            "x3_id": self.x3_id,
            "superuser": self.admin,
            "grp_authorized": self.grp_authorized,
            "msg_login": self.msg_login,
        }

    def __repr__(self) -> str:
        return str(self.to_dict())

    def __str__(self) -> str:
        return str(self.to_dict())

    def _load_from_entry(self, u_entry: Entry):
        if self.username == "":
            self.username = u_entry.username
        self.title = u_entry.title

        for property in u_entry.custom_properties:
            value = u_entry.get_custom_property(property)
            if (not hasattr(self, property) or not value) and not property == "superuser":
                continue

            if property == "superuser":
                self.admin = True if value and value.lower() == "true" else False
            elif property == "msg_login":
                self.msg_login_cust = value
            else:
                setattr(self, property, value)

        tags = u_entry.tags if u_entry.tags else []
        for tag in tags:
            group = tag.lower().strip()
            self.grp_authorized.append(group)

        if self.msg_login_cust:
            self.msg_login = self.msg_login_cust
        elif self.title:
            self.msg_login = f"Bonjour {self.title.split(' ')[0]} !"
        else:
            self.msg_login = "Bonjour !"

    def load(self) -> None:
        if not self.open_db():
            return

        u_entry: Entry | None = None
        for u_entry in self.kee.db.find_entries(username=r".*", group=self.kee_grp, regex=True):
            if u_entry.username.upper() == self.username.upper():
                self.exists = True
                self.is_authorized = True
                self._load_from_entry(u_entry)
                break

    def save(self) -> bool:
        self.open_db(True)

        u_entry: Entry = self.find_user_entry(self.username)
        if u_entry:
            u_entry.username = self.username
            u_entry.title = self.title
        else:
            u_entry: Entry = self.kee.db.add_entry(self.kee_grp, self.title, self.username, password="")

        u_entry.set_custom_property("x3_id", self.x3_id)
        u_entry.set_custom_property("msg_login", self.msg_login_cust)

        if self.admin:
            u_entry.set_custom_property("superuser", "true")
        else:
            u_entry.set_custom_property("superuser", "false")

        u_entry.tags = [grp for grp in self.grp_authorized if not grp == "all"]

        self.kee.save_db()

        self.exists = True
        self.is_authorized = True

        return True

    def delete(self) -> bool:
        self.open_db()

        u_entry: Entry = self.find_user_entry(self.username)
        if u_entry is None:
            raise LookupError("User not found")

        u_entry.delete()
        self.kee.save_db()

        return True


class Server:
    kee: Kee = Kee()
    kee_grp_name: str = "Serveurs"
    kee_grp: Group = None

    @classmethod
    def open_db(cls, reload: bool = False) -> bool:
        cls.kee._open_db(reload)
        if cls.kee.is_ko:
            return False
        cls.kee_grp = cls.kee.db.find_groups(name=cls.kee_grp_name, first=True)
        return True

    def __init__(self, title: str = "", entry: Entry = None):
        self.title: str = ""
        self.user: str = ""
        self.password: str = ""
        self.charset: str = "UTF-8"
        self.database: str = ""
        self.host: str = ""
        self.port: str = "1433"  # string attendu et pas int
        self.server: str = ""
        self.login_timeout: int = 60
        self.timeout: int = 300

        if entry:
            self._load_from_entry(entry)
        else:
            self.title = title if title else "Default"
            self.load()

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "user": self.user,
            "password": self.password,
            "charset": self.charset,
            "database": self.database,
            "host": self.host,
            "port": self.port,
            "server": self.server,
            "login_timeout": self.login_timeout,
            "timeout": self.timeout,
        }

    def __repr__(self) -> str:
        return str(self.to_dict())

    def __str__(self) -> str:
        return str(self.to_dict())

    def _load_from_entry(self, s_entry: Entry):
        self.title = s_entry.title

        self.user = val if (val := s_entry.username) is not None else ""
        self.password = val if (val := s_entry.password) is not None else ""

        for property in s_entry.custom_properties:
            val: str = val if (val := s_entry.get_custom_property(property)) is not None else ""
            if property == "charset":
                val = val.upper() if val else self.charset
            elif property == "login_timeout":
                val = int(val) if val.isdigit() else self.login_timeout
            elif property == "timeout":
                val = int(val) if val.isdigit() else self.timeout

            setattr(self, property, val)

    def load(self):
        if not self.open_db():
            return

        s_entry: Entry = self.kee.db.find_entries(title=self.title, group=self.kee_grp, first=True)
        if s_entry:
            self._load_from_entry(s_entry)

    def reload(self) -> None:
        self.open_db(True)
        self.load()

    def save(self) -> bool:
        self.open_db()

        s_entry: Entry = self.kee.db.find_entries(title=self.title, group=self.kee_grp, first=True)
        if s_entry:
            s_entry.title = self.title
            s_entry.username = self.user
            s_entry.password = self.password
        else:
            s_entry: Entry = self.kee.db.add_entry(self.kee_grp, self.title, self.user, password=self.password)

        for key, val in self.to_dict().items():
            if key in ["title", "user", "password"]:
                continue  # déjà mis à jour avec la création ou recherche de l'entrée
            elif key in ["timeout", "login_timeout"]:
                if val.isdigit():
                    s_entry.set_custom_property(key, val)
                else:
                    return False
            else:
                s_entry.set_custom_property(key, val)

        self.kee.save_db()
        return True


class Settings:
    kee: Kee = Kee()
    kee_grp_name: str = "Paramètres"
    kee_grp: Group = None

    @classmethod
    def open_db(cls, reload: bool = False) -> bool:
        cls.kee._open_db(reload)
        if cls.kee.is_ko:
            return False
        cls.kee_grp = cls.kee.db.find_groups(name=cls.kee_grp_name, first=True)
        return True

    def __init__(self):
        self.app_path: Path = get_app_path()

        self.min_version_settings: str = "9999"  # version minimum requises pour les settings
        self.min_version_pytre: str = "9.999"  # version minimum requises pour Pytre
        self.settings_version: str = ""  # version actuelle des settings

        self.field_separator: str = ""  # délimitateur de champs pour exports
        self.decimal_separator: str = ""  # séparateur décimal pour exports
        self.date_format: str = ""  # format date pour les exports
        self.queries_folder: Path = Path("")  # répertoire des requêtes SQL
        self.extract_folder: Path = Path("")  # répertoire où créer les fichiers des infos extraites

        self.load()  # à charger avant min version pour que queries_folder soit initialisé
        self._init_extract_folder()

        self.server: Server = Server(title="Default")  # objet serveyr
        self.curr_user: User = User()  # objet utilisateur

    def _init_extract_folder(self) -> None:
        self.extract_folder = Path.home() / "Pytre X3 - Extract"
        if not self.extract_folder.exists() or not self.extract_folder.is_dir():
            try:
                self.extract_folder.mkdir(parents=True, exist_ok=True)
            except FileExistsError:
                self.extract_folder = Path.home()

    def load(self) -> None:
        if not self.open_db():
            return

        # key : keepass title, value : settings attribute
        self.params_dict = {
            "FIELD_SEPARATOR": "field_separator",
            "DECIMAL_SEPARATOR": "decimal_separator",
            "DATE_FORMAT": "date_format",
            "QUERIES_FOLDER": "queries_folder",
            "SETTINGS_VERSION": "settings_version",
        }

        for kee_title, attr_name in self.params_dict.items():
            info: Entry = self.kee.db.find_entries(title=kee_title, group=self.kee_grp, first=True)
            setattr(self, attr_name, info.username)

        self.get_min_version()

    def get_min_version(self) -> None:
        file_src = Path(self.queries_folder) / "_version_min.json"
        if file_src.exists():
            with open(file_src, mode="r", encoding="utf-8") as f:
                json_dict = json.load(f)
                self.min_version_pytre = json_dict["pytre_x3"]
                self.min_version_settings = json_dict["settings"]

    def reload(self) -> None:
        self.open_db(True)
        self.load()

    def save(self) -> bool:
        self.open_db()

        for kee_title, attr_name in self.params_dict.items():
            info: Entry = self.kee.db.find_entries(title=kee_title, group=self.kee_grp, first=True)
            val = getattr(self, attr_name)
            info.username = val

        self.kee.save_db()
        return True


if __name__ == "__main__":
    user = User()
    server = Server()
    my_settings = Settings()

    print(f"{'='*50}\nSettings / Infos serveur et version\n{'='*50}")
    print(my_settings.server)
    print(f"Version mini Pytre : {my_settings.min_version_pytre}")
    print(f"Version mini Settings : {my_settings.min_version_settings}")
    print(f"Version utilisée Settings : {my_settings.settings_version}")

    param_attr = ["field_separator", "decimal_separator", "date_format", "queries_folder", "extract_folder"]
    print(f"{'='*50}\nSettings / Paramètres généraux\n{'='*50}")
    for attr in param_attr:
        print(f"- {attr}: {getattr(my_settings, attr)}")

    print(f"{'='*50}\nSettings / Utilisateur courant\n{'='*50}")
    for key, val in my_settings.curr_user.to_dict().items():
        print(f"- {key}: {val}")
