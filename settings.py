import json

config_file = "settings.json"
with open(config_file, mode="r", encoding="utf-8") as f:
    config: dict = json.load(f)

SQL_SERVER: dict = config["SQL_SERVER"]  # paramètres de connection au serveur
FIELD_SEPARATOR: str = config["FIELD_SEPARATOR"]  # délimitateur de champs pour exports
DECIMAL_SEPARATOR: str = config["DECIMAL_SEPARATOR"]  # séparateur décimal pour exports
DATE_FORMAT: str = config["DATE_FORMAT"]  # format date pour les exports
QUERY_FOLDER: str = config["QUERY_FOLDER"]  # répertoire des requêtes SQL
USERS: dict = config["USERS"]  # liste des utilisateurs avec leurs paramètres
