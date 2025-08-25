import json
from pathlib import Path

from pykeepass.entry import Entry
from pykeepass.group import Group

from kee import Kee, get_app_path
from singleton_metaclass import Singleton


class Settings(metaclass=Singleton):
    kee: Kee = Kee()
    kee_grp: Group = None
    logs_are_on: bool = False  # indicateur si logs centraux
    logs_folder: Path = Path("")  # répertoire où stocker les logs centraux

    @classmethod
    def open_db(cls, reload: bool = False) -> bool:
        cls.kee._open_db(reload)
        if cls.kee.is_ko:
            return False
        cls.kee_grp = cls.kee.grp_settings
        return True

    @classmethod
    def set_cls_logs_info(cls, logs_are_on, logs_folder):
        cls.logs_are_on = logs_are_on
        cls.logs_folder = logs_folder

    def __init__(self):
        self.app_path: Path = get_app_path()

        self.min_version_settings: str = "9999"  # version minimum requises pour les settings
        self.min_version_pytre: str = "9.999"  # version minimum requises pour Pytre
        self.settings_version: str = ""  # version actuelle des settings

        self.field_separator: str = ""  # délimitateur de champs pour exports
        self.decimal_separator: str = ""  # séparateur décimal pour exports
        self.date_format: str = ""  # format date pour les exports
        self.queries_folder: Path = Path("")  # répertoire des requêtes SQL

        self.load()  # à charger avant min version pour que queries_folder soit initialisé

    def load(self) -> None:
        if not self.open_db():
            return

        # key : keepass title, value : settings attribute
        self.params_dict = {
            "FIELD_SEPARATOR": "field_separator",
            "DECIMAL_SEPARATOR": "decimal_separator",
            "DATE_FORMAT": "date_format",
            "QUERIES_FOLDER": "queries_folder",
            "LOGS_ARE_ON": "logs_are_on",
            "LOGS_FOLDER": "logs_folder",
            "SETTINGS_VERSION": "settings_version",
        }

        for kee_title, attr_name in self.params_dict.items():
            info: Entry = self.kee.db.find_entries(title=kee_title, group=self.kee_grp, first=True)
            if not info:
                continue

            value: str = info.username
            if kee_title in ("LOGS_ARE_ON",):
                value = True if value.lower() == "true" else False

            setattr(self, attr_name, value)

        self.set_cls_logs_info(self.logs_are_on, self.logs_folder)
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
            if not info:
                info = self.kee.db.add_entry(self.kee_grp, kee_title, username="", password="")

            value = getattr(self, attr_name)
            if kee_title in ("LOGS_ARE_ON",):
                value = "true" if value else "false"

            info.username = value

        self.kee.save_db()
        self.set_cls_logs_info(self.logs_are_on, self.logs_folder)
        return True


if __name__ == "__main__":
    my_settings = Settings()

    print(f"Version mini Pytre : {my_settings.min_version_pytre}")
    print(f"Version mini Settings : {my_settings.min_version_settings}")
    print(f"Version utilisée Settings : {my_settings.settings_version}")

    param_attr = (
        "field_separator",
        "decimal_separator",
        "date_format",
        "queries_folder",
        "logs_are_on",
        "logs_folder",
    )
    print(f"{'='*50}\nSettings / Paramètres généraux\n{'='*50}")
    for attr in param_attr:
        print(f"- {attr}: {getattr(my_settings, attr)}")
