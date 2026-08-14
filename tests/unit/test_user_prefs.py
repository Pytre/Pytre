"""
Tests for Pytre/user_prefs.py

REGRESSION TESTS:
- Issue 2.1: Input validation in user preferences (folder paths)
"""

import pytest
import json
from pathlib import Path
from unittest.mock import patch, mock_open

from Pytre_Core.user_prefs import UserPrefs, PrefsEnum, CACHE_FOLDER


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
# REGRESSION TESTS - Issue 2.1: Input Validation
# ================================================================================


@pytest.mark.regression
def test_set_save_as_folder_validates_path(tmp_path):
    """
    Regression test for Issue 2.1 - save_as_folder must be valid directory

    If path doesn't exist or isn't a directory, should fallback to home.
    """
    prefs = UserPrefs()
    nonexistent = tmp_path / "nonexistent"

    # Set non-existent path
    prefs.set(PrefsEnum.save_as_folder, nonexistent, save=False)

    # Should fallback to home directory
    assert prefs.get(PrefsEnum.save_as_folder) == Path.home()


@pytest.mark.regression
def test_set_save_as_folder_accepts_valid_directory(tmp_path):
    """save_as_folder should accept valid existing directory"""
    prefs = UserPrefs()
    valid_dir = tmp_path / "valid"
    valid_dir.mkdir()

    prefs.set(PrefsEnum.save_as_folder, valid_dir, save=False)

    assert prefs.get(PrefsEnum.save_as_folder) == valid_dir


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
        prefs.set(PrefsEnum.cache_folder, new_folder, save=False)

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


# ================================================================================
# Get/Set Functionality
# ================================================================================


@pytest.mark.unit
def test_set_and_get_last_server():
    """Should be able to set and get last_server"""
    prefs = UserPrefs()

    prefs.set(PrefsEnum.last_server, "TEST_SERVER", save=False)

    assert prefs.get(PrefsEnum.last_server) == "TEST_SERVER"


@pytest.mark.unit
def test_get_with_default():
    """get() should return default if key not set"""
    prefs = UserPrefs()

    # Reset to ensure clean state
    prefs.set(PrefsEnum.last_server, "", save=False)

    result = prefs.get(PrefsEnum.last_server, "default_value")

    # Should return actual value (empty string), not default
    # because key exists
    assert result == ""


@pytest.mark.unit
def test_set_cache_folder_empty_uses_default(tmp_path):
    """Empty cache_folder should use default CACHE_FOLDER"""
    prefs = UserPrefs()

    prefs.set(PrefsEnum.cache_folder, None, save=False)

    assert prefs.get(PrefsEnum.cache_folder) == CACHE_FOLDER


@pytest.mark.unit
def test_set_save_as_folder_empty_uses_home():
    """Empty save_as_folder should use home directory"""
    prefs = UserPrefs()

    prefs.set(PrefsEnum.save_as_folder, None, save=False)

    assert prefs.get(PrefsEnum.save_as_folder) == Path.home()


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


@pytest.mark.unit
def test_save_creates_json_file(tmp_path, monkeypatch):
    """save() should create JSON file with preferences"""
    prefs = UserPrefs()

    # Use temp file
    test_file = tmp_path / "test_prefs.json"
    monkeypatch.setattr(prefs, "file", test_file)

    # Set some values
    prefs.set(PrefsEnum.last_server, "MY_SERVER", save=False)

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
    assert prefs.get(PrefsEnum.last_server) == "LOADED_SERVER"
    assert prefs.get(PrefsEnum.save_as_folder) == tmp_path


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
    assert prefs.get(PrefsEnum.last_server) == ""


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
    assert prefs.get(PrefsEnum.last_server) == ""


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
    prefs.set(PrefsEnum.last_server, "AUTO_SAVED")

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
    prefs.set(PrefsEnum.last_server, "NOT_SAVED", save=False)

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
    prefs.set(PrefsEnum.last_server, "SERVER1")
    mtime1 = test_file.stat().st_mtime

    # Wait a tiny bit
    import time
    time.sleep(0.01)

    # Set same value again
    prefs.set(PrefsEnum.last_server, "SERVER1")
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

    assert prefs.get(PrefsEnum.cache_folder) == CACHE_FOLDER


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
    assert prefs.get(PrefsEnum.cache_folder) == cache_folder
