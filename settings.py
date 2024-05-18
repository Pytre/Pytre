import sys
import os
import getpass

import csv
import json
from pathlib import Path

from pykeepass import PyKeePass
from pykeepass.entry import Entry
from pykeepass.group import Group


def get_app_path() -> Path:
    # If app is run as a bundle then PyInstaller bootloader
    # extends sys with a flag frozen = True and sets app path into _MEIPASS

    if getattr(sys, "frozen", False):
        application_path = sys._MEIPASS
    else:
        application_path = os.path.dirname(os.path.abspath(__file__))

    return Path(application_path)


KEE_FILE = Path().cwd() / "Pytre_X3_Settings.db"
KEE_PWD = r"]i=L'n)X2jg@Y9U82cqy'acn"


class User:
    kee_db: PyKeePass
    kee_file: str = KEE_FILE
    kee_pwd: str = KEE_PWD
    kee_open: bool = False
    users_grp_name: str = "Utilisateurs"
    users_grp: Group

    @classmethod
    def open_db(cls, reload: bool = False):
        if reload or not cls.kee_open:
            cls.kee_db = PyKeePass(cls.kee_file, password=cls.kee_pwd)
            cls.users_grp = cls.kee_db.find_groups(name=cls.users_grp_name, first=True)
            cls.kee_open = True

    @classmethod
    def users_get_all(cls) -> list:
        cls.open_db(True)

        u_entry: Entry | None = None
        u_list: list[User] = []

        for u_entry in cls.kee_db.find_entries(username=r".*", group=cls.users_grp, regex=True):
            u = User(entry=u_entry)
            u_list.append(u)

        return u_list

    @classmethod
    def users_add_groups(cls, usernames: list[str], groups: list[str]):
        cls.open_db(True)

        save: bool = False

        groups = [group for group in groups if not group == "all"]
        u_entry: Entry | None = None
        for u_entry in cls.kee_db.find_entries(username=r".*", group=cls.users_grp, regex=True):
            if u_entry.username not in usernames:
                continue

            new_groups = u_entry.tags if u_entry.tags else []
            new_groups = list(set(new_groups + groups))

            if not new_groups == u_entry.tags:
                u_entry.tags = new_groups
                save = True

        if save:
            cls.kee_db.save()

    @classmethod
    def users_remove_groups(cls, usernames: list[str], groups: list[str]):
        cls.open_db(True)

        save: bool = False

        u_entry: Entry | None = None
        for u_entry in cls.kee_db.find_entries(username=r".*", group=cls.users_grp, regex=True):
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
            cls.kee_db.save()

    @classmethod
    def csv_import(cls, filename: Path, delimiter: str = ";") -> bool:
        cls.open_db(True)

        entries_dict = {}
        u_entry: Entry
        for u_entry in cls.kee_db.find_entries(username=r".*", group=cls.users_grp, regex=True):
            entries_dict[u_entry.username] = u_entry

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
                if row_dict["username"] in entries_dict.keys():
                    u_entry = entries_dict[row_dict["username"]]
                else:
                    u_entry = cls.kee_db.add_entry(User.users_grp, "", row_dict["username"], "")

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
            cls.kee_db.save()

            return True

    @classmethod
    def csv_export(cls, filename: Path, delimiter: str = ";", overwrite: bool = False) -> bool:
        if Path(filename).exists() and not overwrite:
            return False

        rows = [["Id", "Libellé", "Admin", "Groupes", "Id X3", "Login Message"]]
        cls.open_db(True)
        u_entry: Entry | None
        for u_entry in cls.kee_db.find_entries(username=r".*", group=cls.users_grp, regex=True):
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
            self.username = username if username else self._get_username()
            User.open_db()
            self.load()

    def _user_description(self) -> dict:
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
        return str(self._user_description())

    def __str__(self) -> str:
        return str(self._user_description())

    def _get_username(self) -> str:
        domain = os.environ.get("userdnsdomain") or ""
        name = getpass.getuser()
        domain_and_name = f"{domain}\\{name}" if domain else name
        return domain_and_name

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

        if self.msg_login_cust == "":
            self.msg_login = f"Bonjour {self.title.split(' ')[0]} !"
        else:
            self.msg_login = self.msg_login_cust

    def load(self) -> None:
        User.open_db(True)

        u_entry: Entry | None = None
        for u_entry in User.kee_db.find_entries(username=r".*", group=User.users_grp, regex=True):
            if u_entry.username.casefold() == self.username.casefold():
                self.exists = True
                self.is_authorized = True
                self._load_from_entry(u_entry)
                break

    def save(self) -> bool:
        User.open_db()

        u_entry: User = User.kee_db.find_entries(username=self.username, group=User.users_grp, first=True)
        if u_entry:
            u_entry.username = self.username
            u_entry.title = self.title
        else:
            u_entry: Entry = User.kee_db.add_entry(User.users_grp, self.title, self.username, password="")

        u_entry.set_custom_property("x3_id", self.x3_id)
        u_entry.set_custom_property("msg_login", self.msg_login_cust)

        if self.admin:
            u_entry.set_custom_property("superuser", "true")
        else:
            u_entry.set_custom_property("superuser", "false")

        u_entry.tags = [grp for grp in self.grp_authorized if not grp == "all"]

        User.kee_db.save()

        return True

    def delete(self) -> bool:
        User.open_db()

        entry: Entry = User.kee_db.find_entries(username=self.username, group=User.users_grp, first=True)
        if entry is None:
            raise LookupError("User not found")

        entry.delete()
        User.kee_db.save()

        return True


class Settings:
    kee_db: PyKeePass
    kee_file: str = KEE_FILE
    kee_pwd: str = KEE_PWD
    kee_open: bool = False

    servers_group_name: str = "Serveurs"
    servers_group: Group

    params_grp_name: str = "Paramètres"
    params_grp: Group

    @classmethod
    def open_db(cls, reload: bool = False):
        if reload or not cls.kee_open:
            cls.kee_db = PyKeePass(cls.kee_file, password=cls.kee_pwd)

            cls.servers_group = cls.kee_db.find_groups(name=cls.servers_group_name, first=True)
            cls.params_grp = cls.kee_db.find_groups(name=cls.params_grp_name, first=True)

            cls.kee_open = True

    def __init__(self, username: str = ""):
        Settings.open_db()

        self.app_path: Path = get_app_path()

        self.min_version_settings: str = "9999"  # version minimum requises pour les settings
        self.min_version_pytre: str = "9.999"  # version minimum requises pour Pytre
        self.settings_version: str = ""  # version actuelle des settings

        self.sql_server: dict = {}  # paramètres de connection au serveur

        self.curr_user: User = User(username=username)  # objet utilisateur

        self.field_separator: str = ""  # délimitateur de champs pour exports
        self.decimal_separator: str = ""  # séparateur décimal pour exports
        self.date_format: str = ""  # format date pour les exports
        self.queries_folder: Path = Path("")  # répertoire des requêtes SQL
        self.extract_folder: Path = Path("")  # répertoire où créer les fichiers des infos extraites

        self._init_server()
        self._init_params()
        self._init_min_version()
        self._init_extract_folder()

    def _init_server(self):
        s_entry: Entry = Settings.kee_db.find_entries(title="Default", group=Settings.servers_group, first=True)

        self.sql_server["user"] = val if (val := s_entry.username) is not None else ""
        self.sql_server["password"] = val if (val := s_entry.password) is not None else ""

        for property in s_entry.custom_properties:
            self.sql_server[property] = val if (val := s_entry.get_custom_property(property)) is not None else ""

        self.sql_server["charset"] = (
            self.sql_server["charset"].upper() if self.sql_server.get("charset", "") != "" else "UTF-8"
        )

        for key, default in {"timeout": 300, "login_timeout": 60}.items():
            val = self.sql_server[key]
            self.sql_server[key] = int(val) if val.isdigit() else default

    def _init_params(self) -> None:
        # key : keepass title, value : settings attribute
        self.params_dict = {
            "FIELD_SEPARATOR": "field_separator",
            "DECIMAL_SEPARATOR": "decimal_separator",
            "DATE_FORMAT": "date_format",
            "QUERIES_FOLDER": "queries_folder",
            "SETTINGS_VERSION": "settings_version",
        }

        for kee_title, attr_name in self.params_dict.items():
            info: Entry = Settings.kee_db.find_entries(title=kee_title, group=Settings.params_grp, first=True)
            setattr(self, attr_name, info.username)

    def _init_min_version(self) -> None:
        file_src = Path(self.queries_folder) / "_version_min.json"
        if file_src.exists():
            with open(file_src, mode="r", encoding="utf-8") as f:
                json_dict = json.load(f)
                self.min_version_pytre = json_dict["pytre_x3"]
                self.min_version_settings = json_dict["settings"]

    def _init_extract_folder(self) -> None:
        self.extract_folder = Path.home() / "Pytre X3 - Extract"
        if not self.extract_folder.exists() or not self.extract_folder.is_dir():
            try:
                self.extract_folder.mkdir(parents=True, exist_ok=True)
            except FileExistsError:
                self.extract_folder = Path.home()

    def server_reload(self) -> None:
        Settings.kee_db.reload()
        self._init_server()

    def server_save(self) -> bool:
        s_entry: Entry = Settings.kee_db.find_entries(title="Default", group=Settings.servers_group, first=True)

        for key, val in self.sql_server.items():
            if key == "user":
                s_entry.username = val
            elif key == "password":
                s_entry.password = val
            elif key in ["timeout", "login_timeout"]:
                if val.isdigit():
                    s_entry.set_custom_property(key, val)
                else:
                    return False
            else:
                s_entry.set_custom_property(key, val)

        Settings.kee_db.save()
        return True

    def params_reload(self) -> None:
        Settings.kee_db.reload()
        self._init_params()

    def params_save(self) -> bool:
        for kee_title, attr_name in self.params_dict.items():
            info: Entry = Settings.kee_db.find_entries(title=kee_title, group=Settings.params_grp, first=True)
            val = getattr(self, attr_name)
            info.username = val

        Settings.kee_db.save()
        return True


if __name__ == "__main__":
    # my_settings = Settings(user_domain_and_name="PROSOL.PRI\\mferrier")
    my_settings = Settings()

    print(f"{'='*50}\nSettings / Infos serveur et version\n{'='*50}")
    print(my_settings.sql_server)
    print(f"Version mini Pytre : {my_settings.min_version_pytre}")
    print(f"Version mini Settings : {my_settings.min_version_settings}")
    print(f"Version utilisée Settings : {my_settings.settings_version}")

    param_attr = ["field_separator", "decimal_separator", "date_format", "queries_folder", "extract_folder"]
    print(f"{'='*50}\nSettings / Paramètres généraux\n{'='*50}")
    for attr in param_attr:
        print(f"- {attr}: {getattr(my_settings, attr)}")

    print(f"{'='*50}\nSettings / Utilisateur courant\n{'='*50}")
    for key, val in my_settings.curr_user._user_description().items():
        print(f"- {key}: {val}")
