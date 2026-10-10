import json
import logging
import secrets
from enum import Enum
from pathlib import Path, PureWindowsPath

from pykeepass.entry import Entry
from pykeepass.group import Group

from Pytre_Core.kee import Kee
from Pytre_Core.utils import detect_encoding, get_app_path, log as _log, setup_standalone_logging

DEFAULT_QUERIES_FOLDER = Path.home() / "Queries"
DEFAULT_LOGS_FOLDER = Path.home() / "Logs"


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


class _KeepassEnum(Enum):
    """List of keepass entries for Settings.
    Name is the keepass entry title and value is its corresponding settings attribute."""

    QUERIES_FOLDER = "queries_folder"
    LOGS_ARE_ON = "logs_are_on"
    LOGS_FOLDER = "logs_folder"
    SETTINGS_VERSION = "settings_version"
    JWT_SECRET = "jwt_secret"


class Settings:
    def __init__(self, kee: Kee):
        self.kee: Kee = kee
        self.kee_grp: Group | None = None

        self.app_path: Path = get_app_path()

        self.settings_version: str = ""  # version actuelle des settings
        self.queries_folder: Path = DEFAULT_QUERIES_FOLDER  # répertoire des requêtes SQL
        self.logs_are_on: bool = False  # indicateur si logs centraux
        self.logs_folder: Path = DEFAULT_LOGS_FOLDER  # répertoire où stocker les logs centraux

        self.jwt_secret: str = ""  # signature JWT pour Pytre_Server

        self.load()

    def open_db(self, reload: bool = False) -> bool:
        self.kee.open_db(reload)
        if self.kee.is_ko:
            return False
        self.kee_grp = self.kee.grp_settings
        return True

    def _validate_folder(self, folder: str | Path) -> bool:
        """if not valid raise a ValueError"""
        if not Path(folder).is_dir():
            raise ValueError(f"Folder doesn't exist: '{folder}'")

        return True

    def load(self, reload: bool = False) -> None:
        if not self.open_db(reload=reload):
            log("Error, couldn't load settings : base failed to open", logging.ERROR)
            return

        for setting_item in _KeepassEnum:
            kee_title, attr_name = setting_item.name, setting_item.value

            info: Entry = self.kee.db.find_entries(title=kee_title, group=self.kee_grp, first=True)  # type: ignore
            if not info:
                continue

            value = info.username if info.username is not None else ""

            if setting_item is _KeepassEnum.LOGS_ARE_ON:
                value = True if value.lower() == "true" else False
            elif setting_item is _KeepassEnum.LOGS_FOLDER:
                value = Path(value) if value and Path(value).is_dir() else DEFAULT_LOGS_FOLDER
            elif setting_item is _KeepassEnum.QUERIES_FOLDER:
                value = Path(value) if value and Path(value).is_dir() else DEFAULT_QUERIES_FOLDER

            setattr(self, attr_name, value)

        if not self.jwt_secret:
            self.set_jwt_secret()

    def reload(self) -> None:
        self.load(reload=True)

    def set_jwt_secret(self) -> None:
        """Generate and persist a new jwt secret directly.
        Avoid save() so other fields are not changed, revalidated and rewritten."""
        kee_title = _KeepassEnum.JWT_SECRET.name

        self.jwt_secret = secrets.token_urlsafe(32)

        entry = self.kee.db.find_entries(title=kee_title, group=self.kee_grp, first=True)  # type: ignore
        if not isinstance(entry, Entry):
            entry = self.kee.db.add_entry(self.kee_grp, kee_title, username="", password="")  # type: ignore

        entry.username = self.jwt_secret

        self.kee.save_db()

    def save(self) -> bool:
        self.open_db()

        assert self.kee.db is not None
        for setting_item in _KeepassEnum:
            kee_title, attr_name = setting_item.name, setting_item.value

            info = self.kee.db.find_entries(title=kee_title, group=self.kee_grp, first=True)
            if not isinstance(info, Entry):
                info = self.kee.db.add_entry(self.kee_grp, kee_title, username="", password="")

            value = getattr(self, attr_name)
            if setting_item is _KeepassEnum.LOGS_ARE_ON:
                value = "true" if value else "false"
            elif setting_item in (_KeepassEnum.QUERIES_FOLDER, _KeepassEnum.LOGS_FOLDER):
                if setting_item is not _KeepassEnum.LOGS_FOLDER or self.logs_are_on:
                    self._validate_folder(value)
                value = PureWindowsPath(value).as_posix()  # always save path as posix for OSes compatibility

            info.username = str(value)

        self.kee.save_db()
        return True

    def get_min_version(self, queries_folder: str | Path) -> tuple[str, str, bool]:
        """for query_folder return min version needed for application and settings and
        a bool to indicate if retrieving min version failed"""

        if not Path(queries_folder).is_dir():
            raise ValueError(f"Error: folder doesn't exist '{queries_folder}'")

        retrieve_failed: bool = False

        file_src = Path(queries_folder) / "_version_min.json"
        if file_src.exists():
            encoding = detect_encoding(file_src)
            if not encoding:
                raise ValueError(f"Unable to detect encoding for: {file_src}")

            json_dict: dict = {}
            with open(file_src, mode="r", encoding=encoding) as f:
                json_dict = json.load(f)

            min_application = json_dict.get("application", "0.0.0")
            min_settings = json_dict.get("settings", "0")

            if min_application == "0.0.0":
                retrieve_failed = True
                log("Warning: a minimum version is required but couldn't be retrieved for application", logging.ERROR)
            if min_settings == "0":
                retrieve_failed = True
                log("Warning: a minimum version is required but couldn't be retrieved for settings", logging.ERROR)

            return min_application, min_settings, retrieve_failed

        log(f"No minimal version required for queries in {queries_folder}")
        return "0.0.0", "0", retrieve_failed

    def ctrl_version(self, curr_version: str, min_version: str) -> bool:
        """Compare current version vs version min and return True if it is greater or equal"""
        try:
            curr_nums = list(map(lambda i: int(i), curr_version.split(".")))
            min_nums = list(map(lambda i: int(i), min_version.split(".")))
        except ValueError:
            return True  # si erreur format, ne pas bloquer

        max_len = max(len(curr_nums), len(min_nums))
        curr_nums += [0] * (max_len - len(curr_nums))
        min_nums += [0] * (max_len - len(min_nums))

        for i in range(max_len):
            if curr_nums[i] > min_nums[i]:
                return True
            elif curr_nums[i] < min_nums[i]:
                return False

        return True  # égalité


if __name__ == "__main__":
    setup_standalone_logging()

    kee = Kee()
    my_settings = Settings(kee)

    try:
        min_app, min_settings, retrieve_failed = my_settings.get_min_version(my_settings.queries_folder)
    except ValueError as e:
        log(f"Contrôle de version ignoré : {e}")
        min_app, min_settings, retrieve_failed = "0.0.0", "0", True

    print(f"Version mini Application : {min_app}")
    print(f"Version mini Settings : {min_settings}")
    print(f"Version utilisée Settings : {my_settings.settings_version}")

    print(f"{'=' * 50}\nSettings / Paramètres généraux\n{'=' * 50}")
    for setting_item in _KeepassEnum:
        print(f"- {setting_item.name}: {getattr(my_settings, setting_item.value)}")
