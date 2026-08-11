import codecs
import json
import logging
from pathlib import Path, PureWindowsPath

from pykeepass.entry import Entry
from pykeepass.group import Group

from Pytre_Core.kee import Kee
from Pytre_Core.utils import detect_encoding, get_app_path, log as _log, setup_standalone_logging

DEFAULT_QUERIES_FOLDER = Path.home() / "Queries"
DEFAULT_LOGS_FOLDER = Path.home() / "Logs"


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


class Settings:
    def __init__(self, kee: Kee):
        self.kee: Kee = kee
        self.kee_grp: Group | None = None

        self.app_path: Path = get_app_path()
        self.default_encoding: str = "utf-8-sig"  # format à utiliser par défaut

        self.settings_version: str = ""  # version actuelle des settings
        self.field_separator: str = ";"  # délimitateur de champs pour exports
        self.decimal_separator: str = ","  # séparateur décimal pour exports
        self.date_format: str = "dd/MM/yyyy"  # format date pour les exports
        self.extract_encoding: str = self.default_encoding  # encoding format pour les exports
        self.queries_folder: Path = DEFAULT_QUERIES_FOLDER  # répertoire des requêtes SQL
        self.logs_are_on: bool = False  # indicateur si logs centraux
        self.logs_folder: Path = DEFAULT_LOGS_FOLDER  # répertoire où stocker les logs centraux
        self.users_cache_expiration: int = 10  # nombre de jours du cache utilisateur pour ses requêtes

        self._params: dict[str, str] = self._get_params()  # mapping entre keepass title et attributs

        self.load()

    def open_db(self, reload: bool = False) -> bool:
        self.kee._open_db(reload)
        if self.kee.is_ko:
            return False
        self.kee_grp = self.kee.grp_settings
        return True

    def _get_params(self) -> dict[str, str]:
        """key : keepass title, value : settings attribute"""

        return {
            "FIELD_SEPARATOR": "field_separator",
            "DECIMAL_SEPARATOR": "decimal_separator",
            "DATE_FORMAT": "date_format",
            "EXTRACT_ENCODING": "extract_encoding",
            "QUERIES_FOLDER": "queries_folder",
            "LOGS_ARE_ON": "logs_are_on",
            "LOGS_FOLDER": "logs_folder",
            "USERS_CACHE_EXPIRATION": "users_cache_expiration",
            "SETTINGS_VERSION": "settings_version",
        }

    def _validate_encoding_format(self, encoding_format: str) -> bool:
        """if not valid raise a LookupError"""
        try:
            codecs.lookup(encoding_format)
        except LookupError:
            raise LookupError(f"Unknown encoding format: '{encoding_format}'")

        return True

    def load(self, reload: bool = False) -> None:
        if not self.open_db(reload=reload):
            log("Error, couldn't load settings : base failed to open", logging.ERROR)
            return

        for kee_title, attr_name in self._params.items():
            info: Entry = self.kee.db.find_entries(title=kee_title, group=self.kee_grp, first=True)  # type: ignore
            if not info:
                continue

            value: str | int | bool | Path = info.username if info.username is not None else ""

            if kee_title == "FIELD_SEPARATOR" and (not value or len(value) != 1):
                log(f"Invalid field separator '{value}', using default ';'", logging.WARNING)
                value = ";"
            elif kee_title == "DECIMAL_SEPARATOR":
                if not value or len(value) != 1:
                    log(f"Invalid decimal separator '{value}', using default ','")
                    value = ","
                if value == self.field_separator:
                    log(
                        f"Invalid decimal separator '{value}' already used by field separator, using default ','",
                        logging.WARNING,
                    )
                    value = ","
            elif kee_title == "DATE_FORMAT":
                if not value:
                    value = "dd/MM/yyyy"
            elif kee_title == "EXTRACT_ENCODING":
                try:
                    self._validate_encoding_format(value)
                except LookupError:
                    log(
                        f"Invalid encoding settings '{value}', using default '{self.default_encoding}'",
                        logging.WARNING,
                    )
                    value = self.default_encoding
            elif kee_title == "LOGS_ARE_ON":
                value = True if value.lower() == "true" else False
            elif kee_title == "LOGS_FOLDER":
                value = Path(value) if value and Path(value).is_dir() else DEFAULT_LOGS_FOLDER
            elif kee_title == "QUERIES_FOLDER":
                value = Path(value) if value and Path(value).is_dir() else DEFAULT_QUERIES_FOLDER
            elif kee_title == "USERS_CACHE_EXPIRATION":
                if value.isnumeric():
                    value = int(value)
                else:
                    log(
                        f"Invalid users cache expiration '{value}', using '{self.users_cache_expiration}' days",
                        logging.WARNING,
                    )
                    value = self.users_cache_expiration

            setattr(self, attr_name, value)

    def reload(self) -> None:
        self.load(reload=True)

    def save(self) -> bool:
        self.open_db()

        for kee_title, attr_name in self._params.items():
            info: Entry = self.kee.db.find_entries(title=kee_title, group=self.kee_grp, first=True)  # type: ignore
            if not info:
                info = self.kee.db.add_entry(self.kee_grp, kee_title, username="", password="")

            value = getattr(self, attr_name)
            if kee_title == "LOGS_ARE_ON":
                value = "true" if value else "false"
            elif kee_title in ("QUERIES_FOLDER", "LOGS_FOLDER"):
                value = PureWindowsPath(value).as_posix()  # always save path as posix for OSes compatibility
            elif kee_title == "EXTRACT_ENCODING":
                self._validate_encoding_format(value)
            elif kee_title == "USERS_CACHE_EXPIRATION":
                if not str(value).isnumeric():
                    raise ValueError(f"Invalid value for users cache expiration : '{value}'")

            info.username = value

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

    param_attr = my_settings._params.values()
    print(f"{'=' * 50}\nSettings / Paramètres généraux\n{'=' * 50}")
    for attr in param_attr:
        print(f"- {attr}: {getattr(my_settings, attr)}")
