import sys
import os
import getpass
import typing
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


class Settings:
    def __init__(
        self, keepass_file: Path = KEE_FILE, keepass_pwd: str = KEE_PWD, user_name: str = "", user_domain: str = ""
    ):
        self.keepass_db: PyKeePass = PyKeePass(keepass_file, password=keepass_pwd)
        self.app_path: Path = get_app_path()

        self.min_version_settings: str = "9999"  # version minimum requises pour les settings
        self.min_version_pytre: str = "9.999"  # version minimum requises pour Pytre
        self.settings_version: str = ""  # version actuelle des settings
        self.sql_server: dict = {}  # paramètres de connection au serveur
        self.user: User = User(name=user_name, domain=user_domain)  # objet utilisateur
        self.field_separator: str = ""  # délimitateur de champs pour exports
        self.decimal_separator: str = ""  # séparateur décimal pour exports
        self.date_format: str = ""  # format date pour les exports
        self.queries_folder: Path = Path("")  # répertoire des requêtes SQL
        self.extract_folder: Path = Path("")  # répertoire où créer les fichiers des infos extraites
        self.domain_user_auto_add: str = ""  # domaine autorisant l'auto ajout des utilisateurs

        self._init_server()
        self._init_params()
        self._init_min_version()
        self._init_extract_folder()

        self.update_user_infos()

    def _init_server(self):
        s_group = self.keepass_db.find_groups(name="Serveurs", first=True)
        s_entry: Entry = self.keepass_db.find_entries(title="Default", group=s_group, first=True)

        self.sql_server["user"] = val if (val := s_entry.username) is not None else ""
        self.sql_server["password"] = val if (val := s_entry.password) is not None else ""

        for property in s_entry.custom_properties:
            self.sql_server[property] = val if (val := s_entry.get_custom_property(property)) is not None else ""

        self.sql_server["charset"] = (
            self.sql_server["charset"].upper() if self.sql_server.get("charset", "") != "" else "UTF-8"
        )
        self.sql_server["timeout"] = int(self.sql_server.get("timeout", "300"))
        self.sql_server["login_timeout"] = int(self.sql_server.get("login_timeout", "60"))

    def update_user_infos(self) -> None:
        u_group: Group = self.keepass_db.find_groups(name="Utilisateurs", first=True)

        u_entry: Entry | None = None
        for u_entry in self.keepass_db.find_entries(username=r".*", group=u_group, regex=True):
            if u_entry.username.lower() == self.user.domain_and_name.lower():
                break
        else:
            u_entry = None

        user_infos_dict = {}

        if u_entry is not None:
            user_infos_dict["username"] = u_entry.username
            user_infos_dict["title"] = u_entry.title

            for property in u_entry.custom_properties:
                user_infos_dict[property] = val if (val := u_entry.get_custom_property(property)) is not None else ""

            if user_infos_dict.get("superuser", "").lower() == "true":  # valeur str à convertir en bool
                user_infos_dict["superuser"] = True
            else:
                user_infos_dict["superuser"] = False

            user_infos_dict["grp_authorized"] = (
                [item.lower().strip() for item in u_entry.tags] if u_entry.tags is not None else []
            )

            self.user.update_infos(user_infos_dict)

    def _init_params(self) -> None:
        p_group: Group = self.keepass_db.find_groups(name="Paramètres", first=True)

        infos: typing.Dict[str] = {}
        for cust_str in (
            "FIELD_SEPARATOR",
            "DECIMAL_SEPARATOR",
            "DATE_FORMAT",
            "QUERIES_FOLDER",
            "DOMAIN_USER_AUTO_ADD",
            "SETTINGS_VERSION",
        ):
            info: Entry = self.keepass_db.find_entries(title=cust_str, group=p_group, first=True)
            infos[cust_str] = info.username

        self.field_separator = infos["FIELD_SEPARATOR"]
        self.decimal_separator = infos["DECIMAL_SEPARATOR"]
        self.date_format = infos["DATE_FORMAT"]
        self.queries_folder = Path(infos["QUERIES_FOLDER"])
        self.domain_user_auto_add = infos["DOMAIN_USER_AUTO_ADD"]
        self.settings_version = infos["SETTINGS_VERSION"]

    def _init_min_version(self) -> None:
        file_src = self.queries_folder / "_version_min.json"
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

    def create_user(
        self, title: str, username: str, x3_id: str, msg_login: str = "", superuser: str = "false"
    ) -> None:
        group: Group = self.keepass_db.find_groups(name="Utilisateurs", first=True)

        if self.keepass_db.find_entries(username=self.user.domain_and_name, group=group, first=True) is None:
            u_entry: Entry = self.keepass_db.add_entry(group, title, username, password="")
            u_entry.set_custom_property("x3_id", x3_id)
            u_entry.set_custom_property("msg_login", msg_login)
            u_entry.set_custom_property("superuser", superuser)

            self.keepass_db.save()


class User:
    def __init__(self, name="", domain=""):
        self.name: str = name if name else self._get_user_name()
        self.domain: str = domain if domain else self._get_user_domain()
        self.domain_and_name: str = ""

        # création id user qui consiste en la concatenation entre name et domain
        if self.domain:
            self.domain_and_name = f"{self.domain}\\{self.name}"
        else:
            self.domain_and_name = self.name

        self.exist_in_settings: bool = False
        self.is_authorized: bool = False
        self.title: str = ""
        self.x3_id: str = ""
        self.msg_login: str = ""
        self.superuser: bool = False
        self.grp_authorized: list[str] = []

    def _get_user_name(self) -> str:
        return getpass.getuser()

    def _get_user_domain(self) -> str:
        return os.environ.get("userdnsdomain") or ""

    def update_infos(self, infos: dict) -> None:
        # recup des attributs de base attendu
        self.exist_in_settings = True if infos else False
        self.is_authorized = True if infos else False

        self.title = infos.get("title", "")
        self.x3_id = infos.get("x3_id", "")
        self.msg_login = infos.get("msg_login", "")
        self.superuser = infos.get("superuser", False)
        self.grp_authorized = infos.get("grp_authorized", [])

        # recup des autres attributs qui existerait
        self.other_attributes = [attr for attr in infos if not hasattr(self, attr)]
        for attr in self.other_attributes:
            setattr(self, attr, infos.get(attr, ""))

        # si dans les settings pas de message de login alors ajout d'un message par défaut
        if self.msg_login == "":
            self.msg_login = f"Bonjour {self.title.split(' ')[0]} !"


if __name__ == "__main__":
    my_settings = Settings(user_name="mferrier", user_domain="PROSOL.PRI")

    print(my_settings.sql_server)

    print(f"Version mini Pytre : {my_settings.min_version_pytre}")
    print(f"Version mini Settings : {my_settings.min_version_settings}")
    print(f"Version utilisée Settings : {my_settings.settings_version}")

    if my_settings.user.domain:
        print(f"{my_settings.user.name} sur domaine {my_settings.user.domain} :")
    else:
        print(f"{my_settings.user.name} sur aucun domaine :")

    print(f"- autorisation : {my_settings.user.is_authorized}")
    print(f"- superuser : {my_settings.user.superuser}")
    print(f"- login msg : {my_settings.user.msg_login}")
    print(f"- grp_authorized : {my_settings.user.grp_authorized}")
