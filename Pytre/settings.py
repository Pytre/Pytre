import codecs
import json
from pathlib import Path, PureWindowsPath

from pykeepass.entry import Entry
from pykeepass.group import Group

from kee import Kee, get_app_path
from singleton_metaclass import Singleton


class Settings(metaclass=Singleton):
    kee: Kee = Kee()
    kee_grp: Group = None

    @classmethod
    def open_db(cls, reload: bool = False) -> bool:
        cls.kee._open_db(reload)
        if cls.kee.is_ko:
            return False
        cls.kee_grp = cls.kee.grp_settings
        return True

    def __init__(self):
        self.app_path: Path = get_app_path()
        self.default_encoding: str = "utf8-sig"  # format à utiliser par défaut

        self.settings_version: str = ""  # version actuelle des settings
        self.field_separator: str = ""  # délimitateur de champs pour exports
        self.decimal_separator: str = ""  # séparateur décimal pour exports
        self.date_format: str = ""  # format date pour les exports
        self.extract_encoding: str = "utf-8-sig"  # encoding format pour les exports
        self.queries_folder: Path = Path("")  # répertoire des requêtes SQL
        self.logs_are_on: bool = False  # indicateur si logs centraux
        self.logs_folder: Path = Path("")  # répertoire où stocker les logs centraux

        self._params: dict[str, str] = self._get_params()  # mapping entre keepass title et attributs

        self.load()

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
            print("Error, couldn't load settings : base failed to open")
            return

        for kee_title, attr_name in self._params.items():
            info: Entry = self.kee.db.find_entries(title=kee_title, group=self.kee_grp, first=True)
            if not info:
                continue

            value: str = info.username if info.username is not None else ""

            if kee_title == "LOGS_ARE_ON":
                value = True if value.lower() == "true" else False
            elif kee_title == "EXTRACT_ENCODING":
                try:
                    self._validate_encoding_format(value)
                except LookupError:
                    print(f"Invalid encoding settings '{value}', using default '{self.default_encoding}'")
                    value = self.default_encoding
            elif kee_title in ("QUERIES_FOLDER", "LOGS_FOLDER"):
                value = Path(value)

            setattr(self, attr_name, value)

    def reload(self) -> None:
        self.load(reload=True)

    def save(self) -> bool:
        self.open_db()

        for kee_title, attr_name in self._params.items():
            info: Entry = self.kee.db.find_entries(title=kee_title, group=self.kee_grp, first=True)
            if not info:
                info = self.kee.db.add_entry(self.kee_grp, kee_title, username="", password="")

            value = getattr(self, attr_name)
            if kee_title == "LOGS_ARE_ON":
                value = "true" if value else "false"
            elif kee_title in ("QUERIES_FOLDER", "LOGS_FOLDER"):
                value = PureWindowsPath(value).as_posix()  # always save path as posix for OSes compatibility
            elif kee_title == "EXTRACT_ENCODING":
                self._validate_encoding_format(value)

            info.username = value

        self.kee.save_db()
        return True

    def get_min_version(self, queries_folder: Path) -> tuple[str, str]:
        """for query_folder return min version needed for application and settings"""

        if not queries_folder.is_dir():
            raise ValueError(f"Error: folder doesn't exist '{queries_folder}'")

        file_src = Path(queries_folder) / "_version_min.json"
        if file_src.exists():
            with open(file_src, mode="r", encoding="utf-8") as f:
                json_dict: dict = json.load(f)

            min_application = json_dict.get("application", "0.0.0")
            min_settings = json_dict.get("settings", "0")

            if min_application == "0.0.0":
                print("Warning: a minimum version is required but couldn't be retrieved for application")
            if min_settings == "0":
                print("Warning: a minimum version is required but couldn't be retrieved for settings")

            return min_application, min_settings

        print(f"No minimal version required for queries in {queries_folder}")
        return "0.0.0", "0"

    def ctrl_version(self, curr_version: str, min_version: str) -> bool:
        """Compare current version vs version min and return True if it is greater or equal"""
        try:
            curr_nums = list(map(lambda i: int(i), curr_version.split(".")))
            min_nums = list(map(lambda i: int(i), min_version.split(".")))
        except ValueError:
            return True  # si erreur format, ne pas bloquer

        if not len(curr_nums) == len(min_nums):
            return False

        if curr_nums == min_nums:
            return True
        for i in range(len(min_nums)):
            if curr_nums[i] > min_nums[i]:
                return True
            elif curr_nums[i] < min_nums[i]:
                return False

        return False


if __name__ == "__main__":
    my_settings = Settings()

    try:
        min_app, min_settings = my_settings.get_min_version(my_settings.queries_folder)
    except ValueError as e:
        print(f"Contrôle de version ignoré : {e}")
        min_app, min_settings = "0.0.0", "0"

    print(f"Version mini Pytre : {min_app}")
    print(f"Version mini Settings : {min_settings}")
    print(f"Version utilisée Settings : {my_settings.settings_version}")

    param_attr = my_settings._params.values()
    print(f"{'='*50}\nSettings / Paramètres généraux\n{'='*50}")
    for attr in param_attr:
        print(f"- {attr}: {getattr(my_settings, attr)}")
