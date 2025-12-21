import json
from enum import Enum
from pathlib import Path
from typing import Any

from singleton_metaclass import Singleton

EXTRACT_FOLDER: Path = Path.home() / "Pytre"
CONFIG_FOLDER: Path = Path.home() / "Pytre"
SETTING_FILE: Path = CONFIG_FOLDER / "Pytre_Settings.json"


class PrefsEnum(Enum):
    """Names for json and to get/set attributes. Values for internal attribute name"""

    extract_folder = "_extract_folder"
    save_as_folder = "_save_as_folder"
    last_server = "_last_server"


class UserPrefs(metaclass=Singleton):
    def __init__(self):
        self.file: Path = SETTING_FILE
        self._extract_folder: Path = EXTRACT_FOLDER
        self._save_as_folder: Path = EXTRACT_FOLDER
        self._last_server: str = ""
        self._cache: dict[str, Any] = {}

        self.load()

    def __repr__(self) -> str:
        return str(self.to_json())

    def __str__(self) -> str:
        return json.dumps(self.to_json(), indent=2, ensure_ascii=False)

    def _ensure_extract_folder_exists(self) -> bool:
        if self._extract_folder.exists() and self._extract_folder.is_dir():
            return True

        try:
            self._extract_folder.mkdir(parents=True, exist_ok=True)
            return True
        except (OSError, PermissionError) as e:
            print(f"Impossible de créer {self._extract_folder} : {e}")
            if self._extract_folder != EXTRACT_FOLDER:
                self._extract_folder = EXTRACT_FOLDER
                print(f"Utilisation du dossier par défaut: {self._extract_folder}")
            else:
                print("ATTENTION : Le dossier d'extraction par défaut est inaccessible.")

        return False

    def to_json(self) -> dict:
        return {
            PrefsEnum.extract_folder.name: str(self._extract_folder),
            PrefsEnum.save_as_folder.name: str(self._save_as_folder),
            PrefsEnum.last_server.name: self._last_server,
        }

    def set(self, key: PrefsEnum, value: Any, save: bool = True) -> None:
        if not isinstance(key, PrefsEnum):
            raise TypeError(f"'{key}' n'est pas un PrefsEnum valide (type: {type(key).__name__})")
        if not hasattr(self, key.value):
            raise AttributeError(f"Attribut interne manquant : {key.value}")

        if key is PrefsEnum.extract_folder:
            self._extract_folder = Path(value) if value else EXTRACT_FOLDER
            if not self._ensure_extract_folder_exists():
                raise ValueError(f"Valeur invalide pour '{key.value}' : {self._extract_folder}")
        elif key is PrefsEnum.save_as_folder:
            self._save_as_folder = Path(value) if value and Path(value).is_dir() else Path.home()
        else:
            setattr(self, key.value, value)

        if save and self.to_json() != self._cache:
            self.save()

    def get(self, key: PrefsEnum, default: Any = None) -> Any:
        if not isinstance(key, PrefsEnum):
            raise TypeError(f"'{key}' n'est pas un PrefsEnum valide (type: {type(key).__name__})")

        return getattr(self, key.value, default)

    def load(self) -> None:
        json_dict = {}

        if self.file.exists():
            try:
                with open(self.file, mode="r", encoding="utf-8") as f:
                    json_dict: dict = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                print(f"Erreur lors du chargement des préférences utilisateur : {e}")

        for enum in PrefsEnum:
            value = json_dict.get(enum.name, None)
            if value is None:
                continue

            try:
                self.set(enum, value, save=False)
            except (TypeError, ValueError) as e:
                print(f"Valeur invalide pour '{enum.name}': {value} ({e})")

        self._cache = self.to_json()

    def save(self) -> bool:
        data = self.to_json()
        try:
            if not self.file.parent.exists():
                self.file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.file, mode="w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except (OSError, PermissionError) as e:
            print(f"Erreur lors de la sauvegarde des préférences utilisateur: {e}")
            return False

        self._cache = data

        return True


if __name__ == "__main__":
    user_prefs = UserPrefs()
    print(f"{'='*50}\nPreferences utilisateur courant\n{'='*50}")
    print(user_prefs)
