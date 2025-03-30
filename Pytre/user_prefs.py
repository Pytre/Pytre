import json
from enum import Enum
from pathlib import Path

from singleton_metaclass import Singleton

USER_FOLDER: Path = Path.home() / "Pytre"
USER_SETTING_FILE: Path = USER_FOLDER / "Pytre_Settings.json"


class UserPrefsEnum(Enum):
    save_as_folder = "save_as_folder"


class UserPrefs(metaclass=Singleton):
    def __init__(self):
        self.file: Path = USER_SETTING_FILE
        self.extract_folder = self._init_extract_folder()

    def _init_extract_folder(self) -> str:
        extract_folder = USER_FOLDER

        # TODO : migration dossier à retirer dans le futur
        old_folder: Path = Path.home() / "Pytre X3 - Extract"
        if old_folder.exists():
            old_folder.rename(extract_folder)

        if not extract_folder.exists() or not extract_folder.is_dir():
            try:
                extract_folder.mkdir(parents=True, exist_ok=True)
            except FileExistsError:
                extract_folder = Path.home()

        return extract_folder

    def set(self, key: UserPrefsEnum, value: str | int | float | bool | None):
        if key not in UserPrefsEnum:
            raise KeyError(f"'{key}' n'est pas un paramètre existant")

        if not value:
            new_value = ""
        elif not isinstance(value, (str, int, float, bool)):
            new_value = str(value)
        else:
            new_value = value

        json_dict: dict = self._get_all()
        json_dict[key.value] = new_value

        with open(self.file, mode="w", encoding="utf-8") as f:
            json.dump(json_dict, f, indent=4)

    def get(self, key: UserPrefsEnum):
        if key not in UserPrefsEnum:
            raise KeyError(f"'{key}' n'est pas un paramètre existant")

        json_dict: dict = self._get_all()
        return json_dict.get(key.value, None)

    def _get_all(self):
        json_dict: dict = {}

        if self.file.exists():
            with open(self.file, mode="r", encoding="utf-8") as f:
                json_dict: dict = json.load(f)

        return json_dict


if __name__ == "__main__":
    user_prefs = UserPrefs()

    print(f"{'='*50}\nPreferences utilisateur courant\n{'='*50}")
    for pref in UserPrefsEnum:
        print(f"- {pref.value}: {user_prefs.get(pref)}")
