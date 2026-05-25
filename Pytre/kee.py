import sys
import os
from pathlib import Path
from typing import cast

from pykeepass import PyKeePass
from pykeepass.exceptions import CredentialsError
from pykeepass.entry import Entry
from pykeepass.group import Group

from singleton_metaclass import Singleton
from credentials import crypted_file_pwd_get, crypted_file_pwd_history, crypted_file_pwd_change


KEE_FILE: Path = Path().cwd() / "Pytre.db"
BLANK_FILE: str = "res/blank.db"  # relative path for blank db
BLANK_PWD: str = "password"  # password for blank db


def get_app_path() -> Path:
    # If app is run as a bundle then PyInstaller bootloader
    # extends sys with a flag frozen = True and sets app path into _MEIPASS

    if getattr(sys, "frozen", False):
        application_path: str = getattr(sys, "_MEIPASS")
    else:
        application_path: str = os.path.dirname(os.path.abspath(__file__))

    return Path(application_path)


class Kee(metaclass=Singleton):
    def __init__(self, password: str = ""):
        self.file: str | Path = KEE_FILE
        self.pwd: str = password
        self.db: PyKeePass | None = None
        self.is_open: bool = False
        self.is_ko: bool = False
        self.opening_count: int = 0

        self.grp_name_settings: str = "Paramètres"
        self.grp_settings: Group | None = None
        self.grp_name_servers: str = "Serveurs"
        self.grp_servers: Group | None = None
        self.grp_name_users: str = "Utilisateurs"
        self.grp_users: Group | None = None

        if not self.pwd:
            self.pwd = self.pwd_get()
        if not self.is_open and not self.is_ko:
            self._open_db()

    def _open_db(self, reload: bool = False):
        """A utiliser uniquement par la méthode open_db des classes User, Server et Settings"""
        if not self.is_open:
            try:
                self.db = PyKeePass(self.file, password=self.pwd)
            except FileNotFoundError:
                print(f"Base des paramètres introuvable : '{self.file}'")
                self.create_db()
            except CredentialsError:  # si erreur mot de passe, tentative avec les précédents
                self.is_ko = True
                db_unlocked = self.pwd_try()
                if not db_unlocked:
                    print("Accès base des paramètres impossible : mot de passe invalide.")
                    return

            self.is_open = True
            self.opening_count += 1
            print(f"Opening count : {self.opening_count}")
        elif reload and self.db is not None:
            self.db.reload()
            self.opening_count += 1
            print(f"Opening count : {self.opening_count}")

        assert self.db is not None
        self.grp_settings = cast(Group | None, self.db.find_groups(name=self.grp_name_settings, first=True))
        self.grp_servers = cast(Group | None, self.db.find_groups(name=self.grp_name_servers, first=True))
        self.grp_users = cast(Group | None, self.db.find_groups(name=self.grp_name_users, first=True))

    def save_db(self):
        if self.db is None:
            raise RuntimeError("Base non ouverte, impossible de sauvegarder")

        try:
            self.db.save()
        except PermissionError:
            msg = (
                "Vous n'avez les droits d'accès à la base des paramètres !\n"
                + "Vos modifications n'ont pas pu être enregistées.\n\n"
                + "Merci d'alerter les administateurs."
            )
            print(msg)

    def create_db(self, password: str = BLANK_PWD):
        self.is_ko = True

        print("Création d'une nouvelle base en cours...")

        self.db = PyKeePass(get_app_path() / BLANK_FILE, password=BLANK_PWD)
        self.db.filename = self.file
        self.pwd_change(password, True)
        self._create_db_set_default()

        print("Nouvelle base créée ! Mettez à jour les paramètres et changer le mot de passe à l'aide du menu admin.")

        self.is_ko = False

    def _create_db_set_default(self, queries_folder: str = "./queries"):
        assert self.db is not None and isinstance(self.db.root_group, Group)
        self.db.root_group.name = "settings"

        grps: dict[str, Group | None] = {"Paramètres": None, "Serveurs": None, "Utilisateurs": None}
        for grp in grps.keys():
            grps[grp] = self.db.add_group(self.db.root_group, grp)

        Path(queries_folder).mkdir(parents=True, exist_ok=True)

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

    def pwd_try(self, pwds_list: list[str] = []) -> bool:
        if not pwds_list:
            pwds_list = crypted_file_pwd_history()[1:]

        for pwd in pwds_list:
            try:
                self.db = PyKeePass(self.file, password=pwd)
                self.pwd_change(pwd, True)
                self.is_ko = False
                print("Accès à la base restauré")
                return True
            except CredentialsError:
                pass

        return False

    def pwd_get(self) -> str:
        pwd = crypted_file_pwd_get()
        return pwd

    def pwd_history(self) -> list[str]:
        history = crypted_file_pwd_history()
        return history

    def pwd_change(self, new_pwd: str, force_save: bool = False):
        if self.db is None:
            raise RuntimeError("Changement du mot de passe impossible si base non ouverte")

        if new_pwd == self.pwd:
            return

        self.pwd = new_pwd
        self.db.password = new_pwd
        crypted_file_pwd_change(new_pwd)

        if force_save:
            self.save_db()
