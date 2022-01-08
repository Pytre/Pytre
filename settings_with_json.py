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
JSON_FILE = APP_PATH / "settings.json"


class Settings:
    def __init__(self, json_file: Path = JSON_FILE):
        self.json_file: Path = json_file

        self.app_path: Path = get_app_path()

        self.sql_server = {}
        self.users = {}
        self.field_separator: str = ""
        self.decimal_separator: str = ""
        self.date_format: str = ""
        self.queries_folder: Path = Path("")

        self.extract_folder: Path = Path("")

        self._init_values()
        self._init_extract_folder()

    def _init_values(self):
        with open(self.json_file, mode="r", encoding="utf-8") as f:
            obj: dict = json.load(f)

        self.sql_server: dict = obj["SQL_SERVER"]  # paramètres de connection au serveur
        self.users: dict = obj["USERS"]  # liste des utilisateurs avec leurs paramètres

        self.field_separator: str = obj["FIELD_SEPARATOR"]  # délimitateur de champs pour exports
        self.decimal_separator: str = obj["DECIMAL_SEPARATOR"]  # séparateur décimal pour exports
        self.date_format: str = obj["DATE_FORMAT"]  # format date pour les exports
        self.queries_folder: Path() = Path(obj["QUERY_FOLDER"])  # répertoire des requêtes SQL

    def _init_extract_folder(self):
        self.extract_folder = Path.home() / "Pytre"
        if not self.extract_folder.exists() or not self.extract_folder.is_dir():
            try:
                self.extract_folder.mkdir(parents=True, exist_ok=True)
            except FileExistsError:
                self.extract_folder = Path.home()


if __name__ == "__main__":
    my_settings = Settings()
    print(my_settings.sql_server)
    print(my_settings.users)
