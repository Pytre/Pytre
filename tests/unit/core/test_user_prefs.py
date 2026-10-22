"""
Tests for Pytre/user_prefs.py

REGRESSION TESTS:
- Input validation in user preferences (folder paths, date format, decimal separator, csv format, updates)
"""

import dataclasses
import pytest
import json
from pathlib import Path

from Pytre_Core.about import AppStage, AppVersion
from Pytre_Core.user_prefs import (
    DEFAULT_CACHE_DAYS,
    DEFAULT_CSV_DELIMITER,
    DEFAULT_CSV_ENCODING,
    DEFAULT_CSV_QUOTECHAR,
    DEFAULT_DATE_FORMAT,
    DEFAULT_DECIMAL_SEPARATOR,
    CACHE_FOLDER,
    CsvFormat,
    DisplayFormat,
    PrefEnum,
    UserPrefs,
)


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    """
    Fully isolate CONFIG_FOLDER/CACHE_FOLDER/SETTING_FILE inside tmp_path, so migration/folder-
    creation scenarios can be exercised without ever touching the real ~/Pytre_Core.
    Returns (config_folder, cache_folder, setting_file); none of them exist yet.
    """
    config_folder = tmp_path / "Pytre"
    config_folder.mkdir()
    cache_folder = config_folder / ".temp_cache"
    setting_file = config_folder / "Pytre_Settings.json"

    monkeypatch.setattr("Pytre_Core.user_prefs.CONFIG_FOLDER", config_folder)
    monkeypatch.setattr("Pytre_Core.user_prefs.CACHE_FOLDER", cache_folder)
    monkeypatch.setattr("Pytre_Core.user_prefs.SETTING_FILE", setting_file)

    return config_folder, cache_folder, setting_file


# ================================================================================
# REGRESSION TESTS - Input Validation
# ================================================================================


@pytest.mark.regression
def test_set_save_as_folder_validates_path(tmp_path):
    """
    Regression test: save_as_folder must be valid directory

    If path doesn't exist or isn't a directory, should fallback to home.
    """
    prefs = UserPrefs()
    nonexistent = tmp_path / "nonexistent"

    # Set non-existent path
    prefs.set(PrefEnum.SAVE_AS_FOLDER, nonexistent, save=False)

    # Should fallback to home directory
    assert prefs.get(PrefEnum.SAVE_AS_FOLDER) == Path.home()


@pytest.mark.regression
def test_set_save_as_folder_accepts_valid_directory(tmp_path):
    """save_as_folder should accept valid existing directory"""
    prefs = UserPrefs()
    valid_dir = tmp_path / "valid"
    valid_dir.mkdir()

    prefs.set(PrefEnum.SAVE_AS_FOLDER, valid_dir, save=False)

    assert prefs.get(PrefEnum.SAVE_AS_FOLDER) == valid_dir


@pytest.mark.regression
def test_set_cache_folder_nonexistent_raises_error(tmp_path):
    """
    cache_folder is an app-internal folder (renamed from extract_folder to make that
    explicit) - unlike save_as_folder it used to be silently created if missing, but
    set() now requires it to already exist and raises ValueError otherwise. Creation
    is only attempted internally, during UserPrefs.__init__ (see
    test_change_cache_folder_creation_failure_falls_back_to_default below).
    """
    prefs = UserPrefs()
    new_folder = tmp_path / "new_cache"

    with pytest.raises(ValueError, match="Dossier non valide"):
        prefs.set(PrefEnum.CACHE_FOLDER, new_folder, save=False)

    assert not new_folder.exists()


# ================================================================================
# PrefsEnum Validation
# ================================================================================


@pytest.mark.unit
def test_set_invalid_key_raises_typeerror():
    """set() should reject non-PrefsEnum keys"""
    prefs = UserPrefs()

    with pytest.raises(TypeError, match="n'est pas un PrefsEnum valide"):
        prefs.set("not_an_enum", "value", save=False)  # type: ignore


@pytest.mark.unit
def test_get_invalid_key_raises_typeerror():
    """get() should reject non-PrefsEnum keys"""
    prefs = UserPrefs()

    with pytest.raises(TypeError, match="n'est pas un PrefsEnum valide"):
        prefs.get("not_an_enum")  # type: ignore


@pytest.mark.unit
@pytest.mark.usefixtures("isolated_paths")
@pytest.mark.parametrize("pref", list(PrefEnum), ids=lambda pref: pref.name)
def test_pref_enum_matches_user_prefs(pref):
    """Each member must point to an existing UserPrefs attribute and be saved under its json_name"""
    prefs = UserPrefs()

    assert hasattr(prefs, pref.attr_name)
    assert pref.json_name in prefs.to_json()
    assert PrefEnum.from_json_name(pref.json_name) is pref


@pytest.mark.unit
def test_pref_enum_from_json_name_unknown_raises_keyerror():
    with pytest.raises(KeyError):
        PrefEnum.from_json_name("unknown_pref")


# ================================================================================
# Get/Set Functionality
# ================================================================================


@pytest.mark.unit
def test_set_and_get_last_server():
    """Should be able to set and get last_server"""
    prefs = UserPrefs()

    prefs.set(PrefEnum.LAST_SERVER, "TEST_SERVER", save=False)

    assert prefs.get(PrefEnum.LAST_SERVER) == "TEST_SERVER"


@pytest.mark.unit
def test_get_with_default():
    """get() should return default if key not set"""
    prefs = UserPrefs()

    # Reset to ensure clean state
    prefs.set(PrefEnum.LAST_SERVER, "", save=False)

    result = prefs.get(PrefEnum.LAST_SERVER, "default_value")

    # Should return actual value (empty string), not default
    # because key exists
    assert result == ""


@pytest.mark.unit
def test_set_cache_folder_empty_uses_default(tmp_path):
    """Empty cache_folder should use default CACHE_FOLDER"""
    prefs = UserPrefs()

    prefs.set(PrefEnum.CACHE_FOLDER, None, save=False)

    assert prefs.get(PrefEnum.CACHE_FOLDER) == CACHE_FOLDER


@pytest.mark.unit
def test_set_save_as_folder_empty_uses_home():
    """Empty save_as_folder should use home directory"""
    prefs = UserPrefs()

    prefs.set(PrefEnum.SAVE_AS_FOLDER, None, save=False)

    assert prefs.get(PrefEnum.SAVE_AS_FOLDER) == Path.home()


# ================================================================================
# Save/Load Functionality
# ================================================================================


@pytest.mark.unit
def test_to_json_returns_dict():
    """to_json() should return dict with all preferences"""
    prefs = UserPrefs()

    result = prefs.to_json()

    assert isinstance(result, dict)
    assert "cache_folder" in result
    assert "save_as_folder" in result
    assert "last_server" in result
    assert "date_format" in result
    assert "decimal_separator" in result
    assert "csv_delimiter" in result
    assert "csv_quotechar" in result
    assert "csv_encoding" in result
    assert "cache_days" in result


@pytest.mark.unit
def test_save_creates_json_file(tmp_path, monkeypatch):
    """save() should create JSON file with preferences"""
    prefs = UserPrefs()

    # Use temp file
    test_file = tmp_path / "test_prefs.json"
    monkeypatch.setattr(prefs, "file", test_file)

    # Set some values
    prefs.set(PrefEnum.LAST_SERVER, "MY_SERVER", save=False)

    # Save
    success = prefs.save()

    # Verify
    assert success is True
    assert test_file.exists()

    # Read and verify content
    with open(test_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["last_server"] == "MY_SERVER"


@pytest.mark.unit
def test_load_reads_json_file(tmp_path, monkeypatch):
    """load() should read preferences from JSON file"""
    # Create test file
    test_file = tmp_path / "test_prefs.json"
    test_data = {
        "cache_folder": str(tmp_path / "cache"),
        "save_as_folder": str(tmp_path),
        "last_server": "LOADED_SERVER",
    }

    with open(test_file, "w", encoding="utf-8") as f:
        json.dump(test_data, f)

    # Create new prefs instance pointing to test file
    prefs = UserPrefs()
    monkeypatch.setattr(prefs, "file", test_file)

    # Load
    prefs.load()

    # Verify
    assert prefs.get(PrefEnum.LAST_SERVER) == "LOADED_SERVER"
    assert prefs.get(PrefEnum.SAVE_AS_FOLDER) == tmp_path


@pytest.mark.unit
def test_load_handles_missing_file():
    """load() should handle missing file gracefully"""
    prefs = UserPrefs()

    # Point to non-existent file
    prefs.file = Path("/nonexistent/prefs.json")
    # Reset internal state before loading
    prefs._last_server = ""

    # Should not raise error
    prefs.load()

    # Should have default values
    assert prefs.get(PrefEnum.LAST_SERVER) == ""


@pytest.mark.unit
def test_load_handles_invalid_json(tmp_path, monkeypatch):
    """load() should handle corrupted JSON gracefully"""
    test_file = tmp_path / "corrupt.json"
    test_file.write_text("{ invalid json", encoding="utf-8")

    prefs = UserPrefs()
    monkeypatch.setattr(prefs, "file", test_file)
    # Reset internal state before loading
    prefs._last_server = ""

    # Should not raise error
    prefs.load()

    # Should have default values
    assert prefs.get(PrefEnum.LAST_SERVER) == ""


# ================================================================================
# Auto-save Functionality
# ================================================================================


@pytest.mark.unit
def test_set_with_save_true_saves_to_file(tmp_path, monkeypatch):
    """set() with save=True should automatically save to file"""
    prefs = UserPrefs()
    test_file = tmp_path / "auto_save.json"
    monkeypatch.setattr(prefs, "file", test_file)

    # Set with save=True (default)
    prefs.set(PrefEnum.LAST_SERVER, "AUTO_SAVED")

    # File should exist
    assert test_file.exists()

    # Verify content
    with open(test_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["last_server"] == "AUTO_SAVED"


@pytest.mark.unit
def test_set_with_save_false_does_not_save(tmp_path, monkeypatch):
    """set() with save=False should not save to file"""
    prefs = UserPrefs()
    test_file = tmp_path / "no_save.json"
    monkeypatch.setattr(prefs, "file", test_file)

    # Set with save=False
    prefs.set(PrefEnum.LAST_SERVER, "NOT_SAVED", save=False)

    # File should not exist
    assert not test_file.exists()


# ================================================================================
# Cache Functionality
# ================================================================================


@pytest.mark.unit
def test_set_only_saves_if_changed(tmp_path, monkeypatch):
    """set() should only save if value actually changed (cache optimization)"""
    prefs = UserPrefs()
    test_file = tmp_path / "cache_test.json"
    monkeypatch.setattr(prefs, "file", test_file)

    # First save
    prefs.set(PrefEnum.LAST_SERVER, "SERVER1")
    mtime1 = test_file.stat().st_mtime

    # Wait a tiny bit
    import time

    time.sleep(0.01)

    # Set same value again
    prefs.set(PrefEnum.LAST_SERVER, "SERVER1")
    mtime2 = test_file.stat().st_mtime

    # File should NOT have been modified (cache prevents redundant save)
    assert mtime1 == mtime2


# ================================================================================
# String Representation
# ================================================================================


@pytest.mark.unit
def test_repr_returns_dict_string():
    """__repr__ should return dict string"""
    prefs = UserPrefs()
    result = repr(prefs)

    assert isinstance(result, str)
    assert "cache_folder" in result


@pytest.mark.unit
def test_str_returns_json_string():
    """__str__ should return formatted JSON string"""
    prefs = UserPrefs()
    result = str(prefs)

    assert isinstance(result, str)
    # Should be valid JSON
    parsed = json.loads(result)
    assert "cache_folder" in parsed


# ================================================================================
# Error Handling
# ================================================================================


@pytest.mark.unit
def test_save_handles_permission_error(tmp_path, monkeypatch):
    """save() should handle permission errors gracefully"""
    prefs = UserPrefs()

    # Create read-only directory
    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    readonly_file = readonly_dir / "prefs.json"

    monkeypatch.setattr(prefs, "file", readonly_file)

    # Make directory read-only
    readonly_dir.chmod(0o444)

    try:
        # Should not raise error
        success = prefs.save()

        # Should return False
        assert success is False
    finally:
        # Cleanup - restore permissions
        readonly_dir.chmod(0o755)


@pytest.mark.unit
def test_change_cache_folder_creation_failure_falls_back_to_default(tmp_path, monkeypatch):
    """
    If cache folder creation fails during init (init_load=True), should fall back to
    the CACHE_FOLDER default. This is the internal resilience path exercised at
    UserPrefs() startup - the public set() never reaches mkdir (see
    test_set_cache_folder_nonexistent_raises_error above), so it's tested directly
    against _change_cache_folder with init_load=True.
    """
    prefs = UserPrefs()

    # Force a stale, nonexistent current cache folder so the fallback isn't a no-op
    prefs._cache_folder = tmp_path / "stale_nonexistent"

    def mock_mkdir(*args, **kwargs):
        raise PermissionError("Access denied")

    monkeypatch.setattr(Path, "mkdir", mock_mkdir)

    bad_path = tmp_path / "forbidden"
    prefs._change_cache_folder(bad_path, init_load=True)

    assert prefs.get(PrefEnum.CACHE_FOLDER) == CACHE_FOLDER


@pytest.mark.regression
def test_legacy_cache_folder_equal_to_config_folder_triggers_migration(isolated_paths):
    """
    Regression test - migrating from an old version where cache_folder (formerly
    extract_folder) was persisted as CONFIG_FOLDER itself.

    On load, UserPrefs should detect this legacy value, create the new default
    CACHE_FOLDER, and move any CSV files sitting in CONFIG_FOLDER into it - instead
    of just rejecting the value as invalid and leaving the CSVs (and the migration)
    behind.
    """
    config_folder, cache_folder, setting_file = isolated_paths

    setting_file.write_text(
        json.dumps({"cache_folder": str(config_folder), "save_as_folder": str(Path.home()), "last_server": ""})
    )
    old_export = config_folder / "old_export.csv"
    old_export.write_text("a,b,c")

    prefs = UserPrefs()

    assert cache_folder.exists(), "the new default cache folder should have been created"
    assert not old_export.exists(), "the CSV should have been moved out of CONFIG_FOLDER"
    assert (cache_folder / "old_export.csv").exists(), "the CSV should now live in the cache folder"
    assert prefs.get(PrefEnum.CACHE_FOLDER) == cache_folder


# ================================================================================
# DisplayFormat
# ================================================================================


@pytest.mark.unit
def test_display_format_defaults():
    display_format = DisplayFormat()

    assert display_format.date_format == DEFAULT_DATE_FORMAT
    assert display_format.decimal_separator == DEFAULT_DECIMAL_SEPARATOR


@pytest.mark.unit
def test_display_format_to_dict():
    display_format = DisplayFormat(date_format="%Y-%m-%d", decimal_separator=".")

    assert display_format.to_dict() == {"date_format": "%Y-%m-%d", "decimal_separator": "."}
    assert DisplayFormat(**display_format.to_dict()) == display_format


@pytest.mark.unit
def test_display_format_is_frozen():
    display_format = DisplayFormat()

    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(display_format, "date_format", "%Y-%m-%d")


@pytest.mark.unit
@pytest.mark.parametrize("date_format", ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y"])
def test_validate_date_format_valid(date_format):
    assert DisplayFormat.validate_date_format(date_format) is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "date_format",
    [
        "not-a-date",  # no usable directive
        "%m/%Y",  # missing day
        "%d/%m",  # missing year
        "yyyy-MM-dd",  # not a strftime format
        "",
        None,
    ],
)
def test_validate_date_format_invalid_raises_error(date_format):
    with pytest.raises(ValueError, match="Invalid date format"):
        DisplayFormat.validate_date_format(date_format)


@pytest.mark.unit
@pytest.mark.parametrize("decimal_separator", [",", ".", "'"])
def test_validate_decimal_separator_valid(decimal_separator):
    assert DisplayFormat.validate_decimal_separator(decimal_separator) is True


@pytest.mark.unit
@pytest.mark.parametrize("decimal_separator", ["", ",,", None, 1])
def test_validate_decimal_separator_invalid_raises_error(decimal_separator):
    with pytest.raises(ValueError, match="Invalid decimal separator"):
        DisplayFormat.validate_decimal_separator(decimal_separator)


@pytest.mark.unit
def test_display_format_rejects_invalid_values_on_creation():
    with pytest.raises(ValueError, match="Invalid date format"):
        DisplayFormat(date_format="%d/%m")
    with pytest.raises(ValueError, match="Invalid decimal separator"):
        DisplayFormat(decimal_separator=",,")


# ================================================================================
# UserPrefs - date format / decimal separator
# ================================================================================


@pytest.mark.unit
@pytest.mark.usefixtures("isolated_paths")
def test_display_format_defaults_when_not_in_prefs_file():
    prefs = UserPrefs()

    assert prefs.display_format == DisplayFormat()


@pytest.mark.unit
@pytest.mark.usefixtures("isolated_paths")
def test_set_date_format_and_decimal_separator_updates_display_format():
    prefs = UserPrefs()

    prefs.set(PrefEnum.DATE_FORMAT, "%Y-%m-%d", save=False)
    prefs.set(PrefEnum.DECIMAL_SEPARATOR, ".", save=False)

    assert prefs.get(PrefEnum.DATE_FORMAT) == "%Y-%m-%d"
    assert prefs.get(PrefEnum.DECIMAL_SEPARATOR) == "."
    assert prefs.display_format == DisplayFormat(date_format="%Y-%m-%d", decimal_separator=".")


@pytest.mark.unit
@pytest.mark.usefixtures("isolated_paths")
def test_set_invalid_date_format_raises_and_keeps_previous_value():
    prefs = UserPrefs()

    with pytest.raises(ValueError, match="Invalid date format"):
        prefs.set(PrefEnum.DATE_FORMAT, "not-a-date", save=False)

    assert prefs.get(PrefEnum.DATE_FORMAT) == DEFAULT_DATE_FORMAT


@pytest.mark.unit
@pytest.mark.usefixtures("isolated_paths")
def test_set_invalid_decimal_separator_raises_and_keeps_previous_value():
    prefs = UserPrefs()

    with pytest.raises(ValueError, match="Invalid decimal separator"):
        prefs.set(PrefEnum.DECIMAL_SEPARATOR, ",,", save=False)

    assert prefs.get(PrefEnum.DECIMAL_SEPARATOR) == DEFAULT_DECIMAL_SEPARATOR


@pytest.mark.unit
def test_date_format_and_decimal_separator_saved_and_reloaded(isolated_paths):
    _config_folder, _cache_folder, setting_file = isolated_paths

    prefs = UserPrefs()
    prefs.set(PrefEnum.DATE_FORMAT, "%Y-%m-%d", save=False)
    prefs.set(PrefEnum.DECIMAL_SEPARATOR, ".", save=False)
    assert prefs.save() is True

    data = json.loads(setting_file.read_text(encoding="utf-8"))
    assert data["date_format"] == "%Y-%m-%d"
    assert data["decimal_separator"] == "."

    reloaded = UserPrefs()
    assert reloaded.display_format == DisplayFormat(date_format="%Y-%m-%d", decimal_separator=".")


@pytest.mark.unit
def test_load_invalid_date_format_and_decimal_separator_use_defaults(isolated_paths):
    """Invalid values in the prefs file are ignored, defaults are kept"""
    _config_folder, _cache_folder, setting_file = isolated_paths
    setting_file.write_text(json.dumps({"date_format": "yyyy-MM-dd", "decimal_separator": ",,"}), encoding="utf-8")

    prefs = UserPrefs()

    assert prefs.display_format == DisplayFormat()


# ================================================================================
# UserPrefs - csv format / cache expiration
# ================================================================================


@pytest.mark.unit
@pytest.mark.parametrize("value", [";", "|", "\t", " ", '"', "'"])
def test_validate_csv_char_valid(value):
    assert CsvFormat.validate_char(value) is True


@pytest.mark.unit
@pytest.mark.parametrize("value", ["", ";;", "\n", "\r", None, 1])
def test_validate_csv_char_invalid_raises_error(value):
    with pytest.raises(ValueError, match="Invalid csv character"):
        CsvFormat.validate_char(value)


@pytest.mark.unit
@pytest.mark.parametrize(("delimiter", "quotechar"), [(";", '"'), ("|", "'"), ('"', "'"), ("\t", '"')])
def test_validate_csv_dialect_valid(delimiter, quotechar):
    assert CsvFormat.validate_dialect(delimiter, quotechar) is True


@pytest.mark.unit
@pytest.mark.parametrize(("delimiter", "quotechar"), [(";", ";"), ("'", "'"), ("\n", '"'), (";", "\r"), (";;", '"')])
def test_validate_csv_dialect_invalid_raises_error(delimiter, quotechar):
    with pytest.raises(ValueError, match="Invalid csv delimiter"):
        CsvFormat.validate_dialect(delimiter, quotechar)


@pytest.mark.unit
@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "latin-1", "cp1252"])
def test_validate_csv_encoding_valid(encoding):
    assert CsvFormat.validate_encoding(encoding) is True


@pytest.mark.unit
@pytest.mark.parametrize("encoding", ["not-a-real-encoding", "", None])
def test_validate_csv_encoding_invalid_raises_error(encoding):
    with pytest.raises(ValueError, match="Unknown encoding format"):
        CsvFormat.validate_encoding(encoding)


@pytest.mark.unit
@pytest.mark.parametrize(("days", "expected"), [(0, 0), (10, 10), ("0", 0), ("30", 30)])
def test_validate_cache_expiration_days_valid(days, expected):
    assert UserPrefs.validate_cache_expiration_days(days) == expected


@pytest.mark.unit
@pytest.mark.parametrize("days", [-1, "-1", "abc", "", "1.5", 1.5, None, True])
def test_validate_cache_expiration_days_invalid_raises_error(days):
    with pytest.raises(ValueError, match="Invalid cache expiration"):
        UserPrefs.validate_cache_expiration_days(days)


@pytest.mark.unit
@pytest.mark.usefixtures("isolated_paths")
def test_csv_format_and_cache_expiration_defaults():
    prefs = UserPrefs()

    assert prefs.get(PrefEnum.CSV_DELIMITER) == DEFAULT_CSV_DELIMITER
    assert prefs.get(PrefEnum.CSV_QUOTECHAR) == DEFAULT_CSV_QUOTECHAR
    assert prefs.get(PrefEnum.CSV_ENCODING) == DEFAULT_CSV_ENCODING
    assert prefs.get(PrefEnum.CACHE_DAYS) == DEFAULT_CACHE_DAYS


@pytest.mark.unit
@pytest.mark.usefixtures("isolated_paths")
@pytest.mark.parametrize(
    ("key", "value"),
    [
        (PrefEnum.CSV_DELIMITER, ";;"),
        (PrefEnum.CSV_QUOTECHAR, "\n"),
        (PrefEnum.CSV_ENCODING, "not-a-real-encoding"),
        (PrefEnum.CACHE_DAYS, "abc"),
    ],
)
def test_set_invalid_csv_format_or_cache_expiration_raises_and_keeps_previous_value(key, value):
    prefs = UserPrefs()
    previous = prefs.get(key)

    with pytest.raises(ValueError):
        prefs.set(key, value, save=False)

    assert prefs.get(key) == previous


@pytest.mark.unit
@pytest.mark.usefixtures("isolated_paths")
def test_set_csv_chars_individually_allows_swapping_them():
    """Delimiter and quote character are only checked together by CsvFormat.validate_dialect(), so they can be
    swapped one after the other without the first set() being rejected"""
    prefs = UserPrefs()

    prefs.set(PrefEnum.CSV_DELIMITER, '"', save=False)
    prefs.set(PrefEnum.CSV_QUOTECHAR, ";", save=False)

    assert (prefs.get(PrefEnum.CSV_DELIMITER), prefs.get(PrefEnum.CSV_QUOTECHAR)) == ('"', ";")


@pytest.mark.unit
def test_csv_format_defaults():
    csv_format = CsvFormat()

    assert (csv_format.delimiter, csv_format.quotechar, csv_format.encoding) == (
        DEFAULT_CSV_DELIMITER,
        DEFAULT_CSV_QUOTECHAR,
        DEFAULT_CSV_ENCODING,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "kwargs",
    [
        {"delimiter": "'", "quotechar": "'"},  # same character
        {"delimiter": "\n"},
        {"quotechar": ";;"},
        {"encoding": "not-a-real-encoding"},
    ],
)
def test_csv_format_rejects_unusable_values_on_creation(kwargs):
    with pytest.raises(ValueError):
        CsvFormat(**kwargs)


@pytest.mark.unit
def test_csv_format_is_frozen():
    csv_format = CsvFormat()

    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(csv_format, "delimiter", "|")


@pytest.mark.unit
@pytest.mark.usefixtures("isolated_paths")
def test_user_prefs_csv_format_reflects_prefs():
    prefs = UserPrefs()
    prefs.set(PrefEnum.CSV_DELIMITER, "|", save=False)
    prefs.set(PrefEnum.CSV_QUOTECHAR, "'", save=False)
    prefs.set(PrefEnum.CSV_ENCODING, "latin-1", save=False)

    assert prefs.csv_format == CsvFormat(delimiter="|", quotechar="'", encoding="latin-1")


@pytest.mark.unit
@pytest.mark.usefixtures("isolated_paths")
def test_user_prefs_csv_format_falls_back_to_defaults_on_unusable_dialect():
    """Delimiter and quote character are set one at a time, so they can be momentarily unusable together -
    csv_format must still return a usable format rather than raise"""
    prefs = UserPrefs()
    prefs.set(PrefEnum.CSV_DELIMITER, '"', save=False)  # same as the default quote character

    assert prefs.csv_format == CsvFormat()


@pytest.mark.unit
@pytest.mark.usefixtures("isolated_paths")
def test_set_cache_expiration_days_converts_to_int():
    prefs = UserPrefs()

    prefs.set(PrefEnum.CACHE_DAYS, "30", save=False)

    assert prefs.get(PrefEnum.CACHE_DAYS) == 30


@pytest.mark.unit
def test_csv_format_and_cache_expiration_saved_and_reloaded(isolated_paths):
    _config_folder, _cache_folder, setting_file = isolated_paths

    prefs = UserPrefs()
    prefs.set(PrefEnum.CSV_DELIMITER, "|", save=False)
    prefs.set(PrefEnum.CSV_QUOTECHAR, "'", save=False)
    prefs.set(PrefEnum.CSV_ENCODING, "latin-1", save=False)
    prefs.set(PrefEnum.CACHE_DAYS, "30", save=False)
    assert prefs.save() is True

    data = json.loads(setting_file.read_text(encoding="utf-8"))
    assert data["csv_delimiter"] == "|"
    assert data["csv_quotechar"] == "'"
    assert data["csv_encoding"] == "latin-1"
    assert data["cache_days"] == 30

    reloaded = UserPrefs()
    assert reloaded.get(PrefEnum.CSV_DELIMITER) == "|"
    assert reloaded.get(PrefEnum.CSV_QUOTECHAR) == "'"
    assert reloaded.get(PrefEnum.CSV_ENCODING) == "latin-1"
    assert reloaded.get(PrefEnum.CACHE_DAYS) == 30


@pytest.mark.unit
def test_load_unusable_csv_dialect_uses_defaults(isolated_paths):
    """Delimiter and quote character valid on their own but not together fall back to defaults"""
    _config_folder, _cache_folder, setting_file = isolated_paths
    setting_file.write_text(json.dumps({"csv_delimiter": "'", "csv_quotechar": "'"}), encoding="utf-8")

    prefs = UserPrefs()

    assert prefs.get(PrefEnum.CSV_DELIMITER) == DEFAULT_CSV_DELIMITER
    assert prefs.get(PrefEnum.CSV_QUOTECHAR) == DEFAULT_CSV_QUOTECHAR


@pytest.mark.unit
def test_load_invalid_csv_encoding_and_cache_expiration_use_defaults(isolated_paths):
    _config_folder, _cache_folder, setting_file = isolated_paths
    setting_file.write_text(json.dumps({"csv_encoding": "not-a-real-encoding", "cache_days": "abc"}), encoding="utf-8")

    prefs = UserPrefs()

    assert prefs.get(PrefEnum.CSV_ENCODING) == DEFAULT_CSV_ENCODING
    assert prefs.get(PrefEnum.CACHE_DAYS) == DEFAULT_CACHE_DAYS


# ================================================================================
# UserPrefs - updates
# ================================================================================


@pytest.mark.unit
@pytest.mark.usefixtures("isolated_paths")
def test_update_prefs_defaults():
    prefs = UserPrefs()

    assert prefs.get(PrefEnum.UPDATE_CHECK) is True
    assert prefs.get(PrefEnum.UPDATE_CHANNEL) is AppStage.RELEASE
    assert prefs.get(PrefEnum.UPDATE_SKIPPED) == ""


@pytest.mark.unit
@pytest.mark.usefixtures("isolated_paths")
@pytest.mark.parametrize(
    "value, expected",
    [(AppStage.BETA, AppStage.BETA), ("BETA", AppStage.BETA), ("rc", AppStage.RC), (1, AppStage.ALPHA)],
)
def test_set_update_channel_accepts_a_stage_its_name_or_its_number(value, expected):
    prefs = UserPrefs()

    prefs.set(PrefEnum.UPDATE_CHANNEL, value, save=False)

    assert prefs.get(PrefEnum.UPDATE_CHANNEL) is expected


@pytest.mark.unit
@pytest.mark.usefixtures("isolated_paths")
@pytest.mark.parametrize("value", ["gamma", "", 9])
def test_set_invalid_update_channel_raises_and_keeps_previous_value(value):
    prefs = UserPrefs()
    prefs.set(PrefEnum.UPDATE_CHANNEL, AppStage.BETA, save=False)

    with pytest.raises(ValueError):
        prefs.set(PrefEnum.UPDATE_CHANNEL, value, save=False)

    assert prefs.get(PrefEnum.UPDATE_CHANNEL) is AppStage.BETA


@pytest.mark.unit
@pytest.mark.usefixtures("isolated_paths")
@pytest.mark.parametrize("value", [True, False])
def test_set_update_check_accepts_booleans(value):
    prefs = UserPrefs()

    prefs.set(PrefEnum.UPDATE_CHECK, value, save=False)

    assert prefs.get(PrefEnum.UPDATE_CHECK) is value


@pytest.mark.unit
@pytest.mark.usefixtures("isolated_paths")
@pytest.mark.parametrize("value", ["false", "True", 0, 1, None])
def test_set_update_check_rejects_non_booleans(value):
    prefs = UserPrefs()

    with pytest.raises(TypeError, match="Booléen attendu"):
        prefs.set(PrefEnum.UPDATE_CHECK, value, save=False)

    assert prefs.get(PrefEnum.UPDATE_CHECK) is True


@pytest.mark.unit
@pytest.mark.usefixtures("isolated_paths")
@pytest.mark.parametrize(
    "value, expected",
    [("2.0.0-beta.3", "2.0.0-beta.3"), ("2.1.0", "2.1.0"), ("", ""), (None, "")],
)
def test_set_update_skipped_accepts_a_version_or_nothing(value, expected):
    prefs = UserPrefs()

    prefs.set(PrefEnum.UPDATE_SKIPPED, value, save=False)

    assert prefs.get(PrefEnum.UPDATE_SKIPPED) == expected
    if expected:
        assert AppVersion.build_from_string(prefs.get(PrefEnum.UPDATE_SKIPPED)) == AppVersion.build_from_string(value)


@pytest.mark.unit
@pytest.mark.usefixtures("isolated_paths")
@pytest.mark.parametrize("value", ["not a version", "2.0", "2.0.0_BETA.2"])
def test_set_invalid_update_skipped_raises_and_keeps_previous_value(value):
    prefs = UserPrefs()
    prefs.set(PrefEnum.UPDATE_SKIPPED, "2.0.0", save=False)

    with pytest.raises(ValueError):
        prefs.set(PrefEnum.UPDATE_SKIPPED, value, save=False)

    assert prefs.get(PrefEnum.UPDATE_SKIPPED) == "2.0.0"


@pytest.mark.unit
def test_update_prefs_saved_and_reloaded(isolated_paths):
    """The channel is saved by name, never by its number"""
    _config_folder, _cache_folder, setting_file = isolated_paths

    prefs = UserPrefs()
    prefs.set(PrefEnum.UPDATE_CHECK, False, save=False)
    prefs.set(PrefEnum.UPDATE_CHANNEL, AppStage.BETA, save=False)
    prefs.set(PrefEnum.UPDATE_SKIPPED, "2.0.0-beta.3", save=False)
    assert prefs.save() is True

    data = json.loads(setting_file.read_text(encoding="utf-8"))
    assert data["update_check"] is False
    assert data["update_channel"] == "BETA"
    assert data["update_skipped"] == "2.0.0-beta.3"

    reloaded = UserPrefs()
    assert reloaded.get(PrefEnum.UPDATE_CHECK) is False
    assert reloaded.get(PrefEnum.UPDATE_CHANNEL) is AppStage.BETA
    assert reloaded.get(PrefEnum.UPDATE_SKIPPED) == "2.0.0-beta.3"


@pytest.mark.unit
def test_load_invalid_update_prefs_use_defaults(isolated_paths):
    _config_folder, _cache_folder, setting_file = isolated_paths
    invalid = {"update_check": "no", "update_channel": "nightly", "update_skipped": "v.2.0.0_BETA.2"}
    setting_file.write_text(json.dumps(invalid), encoding="utf-8")

    prefs = UserPrefs()

    assert prefs.get(PrefEnum.UPDATE_CHECK) is True
    assert prefs.get(PrefEnum.UPDATE_CHANNEL) is AppStage.RELEASE
    assert prefs.get(PrefEnum.UPDATE_SKIPPED) == ""


@pytest.mark.unit
@pytest.mark.parametrize("pref", list(PrefEnum), ids=lambda pref: pref.name)
def test_pref_enum_attribute_name_is_derived_from_json_name(pref):
    assert pref.json_name == pref.value
    assert pref.attr_name == f"_{pref.json_name}"
