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

# ===============================================================
# récup des settings à partir du fichier json
# ===============================================================
config_file = APP_PATH / "settings.json"
with open(config_file, mode="r", encoding="utf-8") as f:
    config: dict = json.load(f)

# ===============================================================
# puis alimentation des variables avec les infos du fichier json
# ===============================================================
SQL_SERVER: dict = config["SQL_SERVER"]  # paramètres de connection au serveur
FIELD_SEPARATOR: str = config["FIELD_SEPARATOR"]  # délimitateur de champs pour exports
DECIMAL_SEPARATOR: str = config["DECIMAL_SEPARATOR"]  # séparateur décimal pour exports
DATE_FORMAT: str = config["DATE_FORMAT"]  # format date pour les exports
QUERY_FOLDER: Path() = Path(config["QUERY_FOLDER"])  # répertoire des requêtes SQL
# QUERY_FOLDER: str = APP_PATH / config["QUERY_FOLDER"]  # répertoire des requêtes SQL
USERS: dict = config["USERS"]  # liste des utilisateurs avec leurs paramètres
