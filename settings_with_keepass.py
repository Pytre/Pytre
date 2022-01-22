import sys, os
import typing
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


class Settings:
    def __init__(self, keepass_file=KEE_FILE, keepass_pwd=KEE_PWD):
        self.keepass_db: PyKeePass = PyKeePass(keepass_file, password=keepass_pwd)

        self.app_path: Path = get_app_path()

        self.min_version: str = ""  # version minimum requises

        self.sql_server: dict = {}  # paramètres de connection au serveur
        self.users: dict = {}  # liste des utilisateurs avec leurs paramètres
        self.field_separator: str = ""  # délimitateur de champs pour exports
        self.decimal_separator: str = ""  # séparateur décimal pour exports
        self.date_format: str = ""  # format date pour les exports
        self.queries_folder: Path = Path("")  # répertoire des requêtes SQL

        self.extract_folder: Path = Path("")  # répertoire où créer les fichiers des infos extraites

        self._init_server()
        self._init_users()
        self._init_params()
        self._init_extract_folder()

    def _init_server(self):
        s_group = self.keepass_db.find_groups(name="Serveurs", first=True)
        s_entry: Entry = self.keepass_db.find_entries(title="Default", group=s_group, first=True)

        self.sql_server["user"] = val if not (val := s_entry.username) is None else ""
        self.sql_server["password"] = val if not (val := s_entry.password) is None else ""

        for property in s_entry.custom_properties:
            self.sql_server[property] = val if not (val := s_entry.get_custom_property(property)) is None else ""

        self.sql_server["charset"] = (
            self.sql_server["charset"].upper() if not self.sql_server.get("charset", "") == "" else "UTF-8"
        )
        self.sql_server["timeout"] = int(self.sql_server.get("timeout", "300"))
        self.sql_server["login_timeout"] = int(self.sql_server.get("login_timeout", "60"))

    def _init_users(self):
        u_group: Group = self.keepass_db.find_groups(name="Utilisateurs", first=True)
        u_entries: typing.List[Entry] = u_group.entries

        for u_entry in u_entries:
            user = {}
            user_key = u_entry.username

            user["title"] = u_entry.title

            for property in u_entry.custom_properties:
                user[property] = val if not (val := u_entry.get_custom_property(property)) is None else ""

            user["superuser"] = True if user.get("superuser", "").lower() == "true" else False

            self.users[user_key] = user

    def _init_params(self):
        p_group: Group = self.keepass_db.find_groups(name="Paramètres", first=True)

        infos: typing.Dict[str] = {}
        for cust_str in ("FIELD_SEPARATOR", "DECIMAL_SEPARATOR", "DATE_FORMAT", "QUERIES_FOLDER"):
            info: Entry = self.keepass_db.find_entries(title=cust_str, group=p_group, first=True)
            infos[cust_str] = info.username

        self.field_separator = infos["FIELD_SEPARATOR"]
        self.decimal_separator = infos["DECIMAL_SEPARATOR"]
        self.date_format = infos["DATE_FORMAT"]
        self.queries_folder = Path(infos["QUERIES_FOLDER"])

    def _init_min_version(self):
        file = self.queries_folder / "_version_min.txt"
        if file.exists():
            with open(file, mode="r", encoding="utf-8") as f:
                self.min_version = f.readlines()[0]

    def _init_extract_folder(self):
        self.extract_folder = Path.home() / "Pytre X3 - Extract"
        if not self.extract_folder.exists() or not self.extract_folder.is_dir():
            try:
                self.extract_folder.mkdir(parents=True, exist_ok=True)
            except FileExistsError:
                self.extract_folder = Path.home()

    def create_user(self, title: str, username: str, x3_id: str, msg_login: str = "", superuser: str = "false"):
        group: Group = self.keepass_db.find_groups(name="Utilisateurs", first=True)

        u_entry: Entry = self.keepass_db.add_entry(group, title, username, password="")
        u_entry.set_custom_property("x3_id", x3_id)
        u_entry.set_custom_property("msg_login", msg_login)
        u_entry.set_custom_property("superuser", superuser)

        self.keepass_db.save()

        # reinitialisation de la liste des utilisateurs pour la mettre à jour
        self._init_users()


if __name__ == "__main__":
    my_settings = Settings()
    print(my_settings.sql_server)
    print(my_settings.users)
    print(my_settings.min_version)
