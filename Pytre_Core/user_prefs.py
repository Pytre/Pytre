import codecs
import csv
import io
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from Pytre_Core.about import AppStage, AppVersion
from Pytre_Core.app_folders import get_config_folder, SETTINGS_FILENAME
from Pytre_Core.utils import log as _log, setup_standalone_logging


DEFAULT_DATE_FORMAT: str = "%d/%m/%Y"
DEFAULT_DECIMAL_SEPARATOR: str = ","
DEFAULT_CSV_DELIMITER: str = ";"
DEFAULT_CSV_QUOTECHAR: str = '"'
DEFAULT_CSV_ENCODING: str = "utf-8-sig"
DEFAULT_CACHE_DAYS: int = 10


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


class PrefEnum(Enum):
    """Values are the json names - the matching UserPrefs attribute is '_' + json name"""

    DATE_FORMAT = "date_format"
    DECIMAL_SEPARATOR = "decimal_separator"
    CSV_DELIMITER = "csv_delimiter"
    CSV_QUOTECHAR = "csv_quotechar"
    CSV_ENCODING = "csv_encoding"
    CACHE_DAYS = "cache_days"
    SAVE_AS_FOLDER = "save_as_folder"
    LAST_SERVER = "last_server"
    UPDATE_CHECK = "update_check"
    UPDATE_CHANNEL = "update_channel"
    UPDATE_SKIPPED = "update_skipped"

    def __init__(self, json_name: str):
        self.json_name: str = json_name
        self.attr_name: str = f"_{json_name}"

    @classmethod
    def from_json_name(cls, json_name: str) -> PrefEnum:
        """Raises KeyError if `json_name` doesn't match any member"""
        for member in cls:
            if member.json_name == json_name:
                return member
        raise KeyError(json_name)


@dataclass(frozen=True)
class DisplayFormat:
    """How an user types and reads values: used to parse and render query parameters and to format results.
    Raises ValueError if a format is invalid."""

    date_format: str = DEFAULT_DATE_FORMAT
    decimal_separator: str = DEFAULT_DECIMAL_SEPARATOR

    def __post_init__(self) -> None:
        self.validate_date_format(self.date_format)
        self.validate_decimal_separator(self.decimal_separator)

    def to_dict(self) -> dict[str, str]:
        return {"date_format": self.date_format, "decimal_separator": self.decimal_separator}

    @staticmethod
    def validate_date_format(date_format: str) -> bool:
        """if not valid raise a ValueError"""
        if not isinstance(date_format, str) or not date_format:
            raise ValueError(f"Invalid date format: '{date_format}'")

        test_date = datetime(2001, 2, 3)  # day as a valid month value and avoid strptime's defaults values (1/1/1900)

        try:
            formatted = test_date.strftime(date_format)
            parsed = datetime.strptime(formatted, date_format)
            if parsed.date() != test_date.date():
                raise ValueError(f"Invalid date format: '{date_format}' does not round-trip")
        except ValueError as e:
            raise ValueError(f"Invalid date format: '{date_format}'") from e

        return True

    @staticmethod
    def validate_decimal_separator(decimal_separator: str) -> bool:
        """if not valid raise a ValueError"""
        if not isinstance(decimal_separator, str) or len(decimal_separator) != 1:
            raise ValueError(f"Invalid decimal separator: '{decimal_separator}' must be a single character")

        return True


@dataclass(frozen=True)
class CsvFormat:
    """How query results are written to a csv file. Raises ValueError if not usable."""

    delimiter: str = DEFAULT_CSV_DELIMITER
    quotechar: str = DEFAULT_CSV_QUOTECHAR
    encoding: str = DEFAULT_CSV_ENCODING

    def __post_init__(self) -> None:
        self.validate_dialect(self.delimiter, self.quotechar)
        self.validate_encoding(self.encoding)

    @staticmethod
    def validate_encoding(encoding: str) -> bool:
        """if not valid raise a ValueError"""
        try:
            codecs.lookup(encoding)
        except (LookupError, TypeError) as e:
            raise ValueError(f"Unknown encoding format: '{encoding}'") from e

        return True

    @staticmethod
    def validate_char(value: str) -> bool:
        """Checks a csv delimiter or quote character, if not valid raise a ValueError.
        To check both together use validate_dialect()."""
        if not isinstance(value, str) or len(value) != 1 or value in "\r\n":
            raise ValueError(f"Invalid csv character: '{value}' must be a single character and not a line break")

        return True

    @staticmethod
    def validate_dialect(delimiter: str, quotechar: str) -> bool:
        """Checks delimiter and quote character are usable together, if not raise a ValueError"""
        try:
            csv.writer(io.StringIO(), delimiter=delimiter, quotechar=quotechar, quoting=csv.QUOTE_MINIMAL)
        except (TypeError, ValueError) as e:
            raise ValueError(f"Invalid csv delimiter '{delimiter}' and quote character '{quotechar}' ({e})") from e

        return True


class UserPrefs:
    def __init__(self):
        self.file: Path = get_config_folder() / SETTINGS_FILENAME

        self._date_format: str = DEFAULT_DATE_FORMAT
        self._decimal_separator: str = DEFAULT_DECIMAL_SEPARATOR

        self._csv_delimiter: str = DEFAULT_CSV_DELIMITER
        self._csv_quotechar: str = DEFAULT_CSV_QUOTECHAR
        self._csv_encoding: str = DEFAULT_CSV_ENCODING

        self._cache_days: int = DEFAULT_CACHE_DAYS
        self._save_as_folder: Path = Path.home()

        self._last_server: str = ""

        self._update_check: bool = True
        self._update_channel: AppStage = AppStage.RELEASE
        self._update_skipped: str = ""

        self._cache: dict[str, Any] = {}

        self.load()

    @property
    def display_format(self) -> DisplayFormat:
        return DisplayFormat(date_format=self._date_format, decimal_separator=self._decimal_separator)

    @property
    def csv_format(self) -> CsvFormat:
        """Always return a valid CsvFormat object. On invalid values return an object with default one."""
        try:
            return CsvFormat(delimiter=self._csv_delimiter, quotechar=self._csv_quotechar, encoding=self._csv_encoding)
        except ValueError as e:
            log(f"{e}, default csv format will be used", logging.WARNING)
            return CsvFormat()

    def __repr__(self) -> str:
        return str(self.to_json())

    def __str__(self) -> str:
        return json.dumps(self.to_json(), indent=2, ensure_ascii=False)

    def to_json(self) -> dict:
        return {
            PrefEnum.DATE_FORMAT.json_name: self._date_format,
            PrefEnum.DECIMAL_SEPARATOR.json_name: self._decimal_separator,
            PrefEnum.CSV_DELIMITER.json_name: self._csv_delimiter,
            PrefEnum.CSV_QUOTECHAR.json_name: self._csv_quotechar,
            PrefEnum.CSV_ENCODING.json_name: self._csv_encoding,
            PrefEnum.CACHE_DAYS.json_name: self._cache_days,
            PrefEnum.SAVE_AS_FOLDER.json_name: str(self._save_as_folder),
            PrefEnum.LAST_SERVER.json_name: self._last_server,
            PrefEnum.UPDATE_CHECK.json_name: self._update_check,
            PrefEnum.UPDATE_CHANNEL.json_name: self._update_channel.name,
            PrefEnum.UPDATE_SKIPPED.json_name: self._update_skipped,
        }

    def set(self, key: PrefEnum, value: Any, save: bool = True) -> str:
        warning = ""

        if not isinstance(key, PrefEnum):
            raise TypeError(f"'{key}' n'est pas un PrefsEnum valide (type: {type(key).__name__})")
        if not hasattr(self, key.attr_name):
            raise AttributeError(f"Attribut interne manquant : {key.attr_name}")

        if key is PrefEnum.SAVE_AS_FOLDER:
            self._save_as_folder = Path(value) if value and Path(value).is_dir() else Path.home()
        elif key is PrefEnum.DATE_FORMAT:
            DisplayFormat.validate_date_format(value)
            self._date_format = value
        elif key is PrefEnum.DECIMAL_SEPARATOR:
            DisplayFormat.validate_decimal_separator(value)
            self._decimal_separator = value
        elif key is PrefEnum.CSV_DELIMITER:
            CsvFormat.validate_char(value)
            self._csv_delimiter = value
        elif key is PrefEnum.CSV_QUOTECHAR:
            CsvFormat.validate_char(value)
            self._csv_quotechar = value
        elif key is PrefEnum.CSV_ENCODING:
            CsvFormat.validate_encoding(value)
            self._csv_encoding = value
        elif key is PrefEnum.CACHE_DAYS:
            self._cache_days = self.validate_cache_expiration_days(value)
        elif key is PrefEnum.UPDATE_CHECK:
            if not isinstance(value, bool):
                raise TypeError(f"Booléen attendu pour '{key.json_name}' : {value!r}")
            self._update_check = value
        elif key is PrefEnum.UPDATE_CHANNEL:
            self._update_channel = AppStage.from_value(value)
        elif key is PrefEnum.UPDATE_SKIPPED:
            if value:
                AppVersion.build_from_string(value)  # raises ValueError on an invalid version
            self._update_skipped = str(value or "")
        else:
            setattr(self, key.attr_name, value)

        if save and self.to_json() != self._cache:
            self.save()

        return warning

    def get(self, key: PrefEnum, default: Any = None) -> Any:
        if not isinstance(key, PrefEnum):
            raise TypeError(f"'{key}' n'est pas un PrefsEnum valide (type: {type(key).__name__})")

        return getattr(self, key.attr_name, default)

    def load(self) -> None:
        json_dict = {}

        if self.file.exists():
            try:
                with open(self.file, mode="r", encoding="utf-8") as f:
                    json_dict: dict = {k.lower(): v for k, v in json.load(f).items()}  # force key to lower case
            except (json.JSONDecodeError, IOError) as e:
                log(f"Erreur lors du chargement des préférences utilisateur : {e}", logging.WARNING)

        for enum in PrefEnum:
            value = json_dict.get(enum.json_name, None)
            if value is None:
                continue

            try:
                self.set(enum, value, save=False)
            except (TypeError, ValueError) as e:
                log(f"Valeur invalide pour '{enum.json_name}': {value} ({e})", logging.WARNING)

        try:
            CsvFormat.validate_dialect(self._csv_delimiter, self._csv_quotechar)
        except ValueError as e:
            log(f"{e}, valeurs par défaut utilisées", logging.WARNING)
            self._csv_delimiter, self._csv_quotechar = DEFAULT_CSV_DELIMITER, DEFAULT_CSV_QUOTECHAR

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

    @staticmethod
    def validate_cache_expiration_days(days: int | str) -> int:
        """Returns `days` as an int, if not a positive or zero integer raise a ValueError"""
        valid_int = isinstance(days, int) and not isinstance(days, bool) and days >= 0
        if not (valid_int or (isinstance(days, str) and days.isdigit())):
            raise ValueError(f"Invalid cache expiration: '{days}' must be a number of days")

        return int(days)


if __name__ == "__main__":
    setup_standalone_logging()
    user_prefs = UserPrefs()
    print(f"{'=' * 50}\nPreferences utilisateur courant\n{'=' * 50}")
    print(user_prefs)
