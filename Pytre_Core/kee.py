import logging
from enum import Enum
from pathlib import Path
from typing import cast

from pykeepass import PyKeePass
from pykeepass.exceptions import CredentialsError
from pykeepass.group import Group

from Pytre_Core.utils import get_app_path, log as _log
from Pytre_Core.credentials import crypted_file_pwd_get, crypted_file_pwd_change


KEE_FILE: Path = Path().cwd() / "Pytre.db"
BLANK_FILE: str = "res/blank.db"  # relative path for blank db
BLANK_PWD: str = "password"  # password for blank db


class KeeGroup(Enum):
    SETTINGS = "Paramètres"
    SERVERS = "Serveurs"
    USERS = "Utilisateurs"
    OIDC = "OidcProviders"


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


class Kee:
    def __init__(self, password: str = ""):
        """`password` also serve, instead of `BLANK_PWD`, to set a new base if it's needed.
        Without `password` it is read from the encrypted key file, which then records password change
        (hybrid client). With `password` (server: from the keyring) the key file is never used."""

        self._init_password: str = password or BLANK_PWD  # will be used to create db if it doesn't exists
        self._uses_key_file: bool = not password

        self.file: str | Path = KEE_FILE
        self.password: str = password
        self.db: PyKeePass | None = None
        self.is_open: bool = False
        self.is_ko: bool = False
        self.access_count: int = 0

        self.grp_settings: Group | None = None
        self.grp_servers: Group | None = None
        self.grp_users: Group | None = None
        self.grp_oidc: Group | None = None

        if not self.password:
            self.password = self.pwd_get()
        if not self.is_open and not self.is_ko:
            self.open_db()

    def open_db(self, reload: bool = False):
        if not self.is_open:
            try:
                self.db = PyKeePass(self.file, password=self.password)
            except FileNotFoundError:
                log(f"Settings database not found: '{self.file}'", logging.WARNING)
                self.create_db(password=self._init_password)
            except CredentialsError:
                self.is_ko = True
                log("Failed to access settings database: invalid password.", logging.ERROR)
                return

            self.is_open = True
            self.access_count += 1
            log(f"Database opened, access count : {self.access_count}", logging.DEBUG)
        elif reload and self.db is not None:
            self.db.reload()
            self.access_count += 1
            log(f"Database reloaded, access count : {self.access_count}", logging.DEBUG)

        self.grp_settings = self._get_group(KeeGroup.SETTINGS.value)
        self.grp_servers = self._get_group(KeeGroup.SERVERS.value)
        self.grp_users = self._get_group(KeeGroup.USERS.value)
        self.grp_oidc = self._get_group(KeeGroup.OIDC.value)

    def save_db(self) -> bool:
        """Returns whether the database was saved"""
        if self.db is None:
            raise RuntimeError("Can't save a closed database.")

        try:
            self.db.save()
        except PermissionError as e:
            log(f"Failed to save settings database: {e}", logging.ERROR)
            return False

        return True

    def create_db(self, password: str = BLANK_PWD):
        self.is_ko = True

        log("Creating new settings database...")

        self.db = PyKeePass(get_app_path() / BLANK_FILE, password=BLANK_PWD)
        self.db.filename = self.file
        self.pwd_change(password, True)

        log("New settings database created. Update parameters and change its password via admin menu.")

        self.is_ko = False

    def pwd_try(self, password: str) -> bool:
        """Tries to open the database with `password`, if it works it becomes the current one"""
        try:
            self.db = PyKeePass(self.file, password=password)
            self.pwd_change(password, True)
            self.is_ko = False
            log("Database access restored.")
            return True
        except CredentialsError:
            pass

        return False

    def pwd_get(self) -> str:
        pwd = crypted_file_pwd_get()
        return pwd

    def pwd_change(self, new_password: str, force_save: bool = False) -> None:
        """Saves the database encrypted with `new_password`, then records it in the key file - in
        that order, so the key file never holds a password the database isn't encrypted with.
        Does nothing if the password is unchanged, unless `force_save` is True.
        Raises RuntimeError if the database can't be saved, the previous password is then kept."""

        if self.db is None:
            raise RuntimeError("Failed to change the password. Settings database not open.")

        if not force_save and new_password == self.password:
            return

        previous_password, previous_db_password = self.password, self.db.password
        self.password, self.db.password = new_password, new_password

        if not self.save_db():
            self.password, self.db.password = previous_password, previous_db_password
            raise RuntimeError("Failed to save the settings database with its new password")

        if self._uses_key_file:
            crypted_file_pwd_change(new_password)

    def _get_group(self, name: str) -> Group:
        """Return database group. If it doesn't exist it will be created."""

        if self.db is None:
            raise ValueError("Can't retrieve a group without an opened database.")

        group = cast(Group | None, self.db.find_groups(name=name, first=True))
        if not group:
            group = self.db.add_group(self.db.root_group, name)

        return group
