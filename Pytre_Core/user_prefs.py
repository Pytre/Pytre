import json
import logging
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from Pytre_Core.utils import log as _log, setup_standalone_logging

CACHE_FOLDER: Path = Path.home() / "Pytre" / ".temp_cache"
CONFIG_FOLDER: Path = Path.home() / "Pytre"
SETTING_FILE: Path = CONFIG_FOLDER / "Pytre_Settings.json"


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


class PrefsEnum(Enum):
    """Names for json and to get/set attributes. Values for internal attribute name"""

    cache_folder = "_cache_folder"
    save_as_folder = "_save_as_folder"
    last_server = "_last_server"


class IssueType(Enum):
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Issue:
    type: IssueType
    msg: str


class UserPrefs:
    def __init__(self):
        self.file: Path = SETTING_FILE
        self._cache_folder: Path = CACHE_FOLDER
        self._save_as_folder: Path = Path.home()
        self._last_server: str = ""

        self._cache: dict[str, Any] = {}

        self.load()
        self._change_cache_folder(self._cache_folder, init_load=True)  # make sure cache folder exists

    def __repr__(self) -> str:
        return str(self.to_json())

    def __str__(self) -> str:
        return json.dumps(self.to_json(), indent=2, ensure_ascii=False)

    def _change_cache_folder(self, folder: Path, init_load: bool = False) -> Issue | None:
        """Validates and applies `folder` as the new cache folder.
        Returns None on success or an Issue describing what went wrong (no raise on error)"""

        if folder.resolve() == CONFIG_FOLDER.resolve():
            if init_load:
                self._migrate_cache_from_config_folder()
                return None
            return Issue(
                type=IssueType.ERROR, msg="Le dossier cache ne peut pas être identique au dossier de configuration."
            )

        if not init_load and folder.exists() and folder not in (self._cache_folder, CACHE_FOLDER):
            self._cache_folder = folder
            if any(folder.iterdir()):
                return Issue(
                    type=IssueType.WARNING,
                    msg="Le dossier cache est nettoyé automatiquement à intervalle régulier. "
                    + "Des fichiers existent dans le dossier choisi et ils seront supprimés "
                    + "lors d'une prochaine ouverture de l'application.",
                )
            return None

        if folder.exists() and folder.is_dir():
            self._cache_folder = folder
            return None
        elif not init_load:
            return Issue(type=IssueType.ERROR, msg=f"Dossier non valide : {self._cache_folder}")

        try:
            folder.mkdir(parents=True, exist_ok=True)
            self._cache_folder = folder
            return None
        except (OSError, PermissionError) as e:
            err_msg = f"Impossible de créer {folder} : {e}"
            log(err_msg, logging.WARNING)
            if folder != self._cache_folder and self._cache_folder.exists() and self._cache_folder.is_dir():
                log(f"Dossier actuel conservé : {self._cache_folder}")
            elif folder != self._cache_folder and folder != CACHE_FOLDER:
                log("Tentative utilisation dossier cache par défaut...")
                return self._change_cache_folder(CACHE_FOLDER)
            else:
                log("ATTENTION : Le dossier de cache par défaut est inaccessible.", logging.ERROR)

            return Issue(type=IssueType.ERROR, msg=f"{err_msg}\nDossier cache retenu : {self._cache_folder}")

    def _migrate_cache_from_config_folder(self) -> None:
        """Move extracted files from CONFIG_FOLDER to CACHE_FOLDER"""

        log("Migration du cache : déplacement des fichiers CSV de CONFIG_FOLDER vers CACHE_FOLDER...")
        if self._change_cache_folder(CACHE_FOLDER, init_load=True) is not None:
            log("Migration annulée : impossible de créer le dossier cache par défaut.", logging.ERROR)
            return

        moved = 0
        for csv_file in CONFIG_FOLDER.glob("*.csv"):
            try:
                shutil.move(str(csv_file), self._cache_folder / csv_file.name)
                moved += 1
            except (OSError, shutil.Error) as e:
                log(f"Impossible de déplacer {csv_file.name} : {e}", logging.ERROR)

        log(f"Migration terminée : {moved} fichier(s) déplacé(s) vers {self._cache_folder}")
        self.save()

    def _set_cache_folder(self, folder: str | Path | None, init_load: bool) -> str:
        """Handle for set(). Raises ValueError when _change_cache_folder() returns an ERROR Issue,
        otherwise returns a possibly empty warning string"""

        pref_name = PrefsEnum.cache_folder.name
        warning = ""

        old_cache_folder = self._cache_folder
        new_cache_folder: Path = Path(folder) if folder else CACHE_FOLDER

        result = self._change_cache_folder(new_cache_folder, init_load=init_load)
        if result is not None:
            if result.type == IssueType.ERROR:
                log(f"Valeur invalide '{pref_name}' : {new_cache_folder}\n{result.msg}", logging.ERROR)
                raise ValueError(result.msg)
            if result.type == IssueType.WARNING:
                log(f"Warning '{pref_name}' : {result.msg}", logging.WARNING)
                warning = result.msg
        elif old_cache_folder.resolve() != self._cache_folder.resolve():
            warning = "Le contenu du dossier cache est temporaire et nettoyé automatiquement."

        return warning

    def to_json(self) -> dict:
        return {
            PrefsEnum.cache_folder.name: str(self._cache_folder),
            PrefsEnum.save_as_folder.name: str(self._save_as_folder),
            PrefsEnum.last_server.name: self._last_server,
        }

    def set(self, key: PrefsEnum, value: Any, save: bool = True, init_load: bool = False) -> str:
        warning = ""

        if not isinstance(key, PrefsEnum):
            raise TypeError(f"'{key}' n'est pas un PrefsEnum valide (type: {type(key).__name__})")
        if not hasattr(self, key.value):
            raise AttributeError(f"Attribut interne manquant : {key.value}")

        if key is PrefsEnum.cache_folder:
            warning = self._set_cache_folder(value, init_load=init_load)
        elif key is PrefsEnum.save_as_folder:
            self._save_as_folder = Path(value) if value and Path(value).is_dir() else Path.home()
        else:
            setattr(self, key.value, value)

        if save and self.to_json() != self._cache:
            self.save()

        return warning

    def get(self, key: PrefsEnum, default: Any = None) -> Any:
        if not isinstance(key, PrefsEnum):
            raise TypeError(f"'{key}' n'est pas un PrefsEnum valide (type: {type(key).__name__})")

        return getattr(self, key.value, default)

    def load(self) -> None:
        json_dict = {}

        if self.file.exists():
            try:
                with open(self.file, mode="r", encoding="utf-8") as f:
                    json_dict: dict = {k.lower(): v for k, v in json.load(f).items()}  # force key to lower case
            except (json.JSONDecodeError, IOError) as e:
                log(f"Erreur lors du chargement des préférences utilisateur : {e}", logging.WARNING)

        for enum in PrefsEnum:
            value = json_dict.get(enum.name, None)
            if value is None:
                continue

            try:
                self.set(enum, value, save=False, init_load=True)
            except (TypeError, ValueError) as e:
                log(f"Valeur invalide pour '{enum.name}': {value} ({e})", logging.WARNING)

        self._cache = self.to_json()

    def save(self) -> bool:
        data = self.to_json()
        try:
            if not self.file.parent.exists():
                self.file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.file, mode="w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except (OSError, PermissionError) as e:
            log(f"Erreur lors de la sauvegarde des préférences utilisateur: {e}", logging.ERROR)
            return False

        self._cache = data

        return True


if __name__ == "__main__":
    setup_standalone_logging()
    user_prefs = UserPrefs()
    print(f"{'=' * 50}\nPreferences utilisateur courant\n{'=' * 50}")
    print(user_prefs)
