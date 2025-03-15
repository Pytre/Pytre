import sys
import os

from pathlib import Path
from tkinter import messagebox

from pykeepass import PyKeePass
from pykeepass.exceptions import CredentialsError
from pykeepass.entry import Entry

from ui.InputDialog import InputDialog
from credentials import crypted_file_pwd_get, crypted_file_pwd_history, crypted_file_pwd_change


KEE_FILE: Path = Path().cwd() / "Pytre.db"
BLANK_FILE: str = "res/blank.db"  # relative path for blank db
BLANK_PWD: str = "password"  # password for blank db


def get_app_path() -> Path:
    # If app is run as a bundle then PyInstaller bootloader
    # extends sys with a flag frozen = True and sets app path into _MEIPASS

    if getattr(sys, "frozen", False):
        application_path = sys._MEIPASS
    else:
        application_path = os.path.dirname(os.path.abspath(__file__))

    return Path(application_path)


class Singleton(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)

        return cls._instances[cls]


class Kee(metaclass=Singleton):
    def __init__(self):
        self.file: str = KEE_FILE
        self.pwd: str = ""
        self.db: PyKeePass = None
        self.is_open: bool = False
        self.is_ko: bool = False
        self.opening_count: int = 0

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
