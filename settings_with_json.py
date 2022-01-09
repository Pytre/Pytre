import sys, os
import json
from pathlib import Path


def get_app_path() -> Path:
    # If app is run as a bundle then PyInstaller bootloader
    # extends sys with a flag frozen = True and sets app path into _MEIPASS

    if getattr(sys, "frozen", False):
        application_path = sys._MEIPASS
    else:
        application_path = os.path.dirname(os.path.abspath(__file__))

    return Path(application_path)


APP_PATH = get_app_path()
JSON_FILE = APP_PATH / "Pytre_X3_Settings.json"


class Settings:
    def __init__(self, json_file: Path = JSON_FILE):
        self.json_file: Path = json_file

        self.app_path: Path = get_app_path()

        self.min_version: str = ""  # version minimum requises

        self.sql_server: dict = {}  # paramètres de connection au serveur
        self.users: dict = {}  # liste des utilisateurs avec leurs paramètres
        self.field_separator: str = ""  # délimitateur de champs pour exports
        self.decimal_separator: str = ""  # séparateur décimal pour exports
        self.date_format: str = ""  # format date pour les exports
        self.queries_folder: Path = Path("")  # répertoire des requêtes SQL

        self.extract_folder: Path = Path("")  # répertoire où créer les fichiers des infos extraites

        self._init_values()
        self._init_min_version()
        self._init_extract_folder()

    def _init_values(self):
        with open(self.json_file, mode="r", encoding="utf-8") as f:
            obj: dict = json.load(f)

        self.sql_server = obj["SQL_SERVER"]
        self.users = obj["USERS"]

        self.field_separator = obj["FIELD_SEPARATOR"]
        self.decimal_separator = obj["DECIMAL_SEPARATOR"]
        self.date_format = obj["DATE_FORMAT"]
        self.queries_folder = Path(obj["QUERY_FOLDER"])

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


if __name__ == "__main__":
    my_settings = Settings()
    print(my_settings.sql_server)
    print(my_settings.users)
    print(my_settings.min_version)
