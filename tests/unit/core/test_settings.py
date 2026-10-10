"""
Tests for Pytre/settings.py

REGRESSION TESTS:
- Input validation for paths
- Kee initialized in __init__ (not at class level)
"""

import pytest
import json
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from Pytre_Core.settings import Settings, DEFAULT_QUERIES_FOLDER, DEFAULT_LOGS_FOLDER


# ================================================================================
# Fixtures
# ================================================================================


@pytest.fixture
def mock_kee():
    """Mock Kee object with minimal structure"""
    mock = Mock()
    mock.is_ko = False
    mock.db = Mock()
    mock.db.find_entries = Mock(return_value=None)
    mock.grp_settings = Mock()
    mock.open_db = Mock(return_value=True)
    mock.save_db = Mock()
    return mock


@pytest.fixture
def mock_kee_entry():
    """Mock KeePass entry"""
    entry = Mock()
    entry.username = None
    entry.password = ""
    return entry


# ================================================================================
# REGRESSION TESTS - Dependency Injection
# ================================================================================


@pytest.mark.regression
def test_kee_injected_via_constructor(mock_kee):
    """
    Regression test: Kee is injected via the constructor

    Settings no longer constructs its own Kee (removed with the singleton
    pattern) - the caller (AppContext) builds and passes it in.
    """
    settings = Settings(mock_kee)

    assert settings.kee is mock_kee


# ================================================================================
# REGRESSION TESTS - Input Validation
# ================================================================================


@pytest.mark.regression
def test_queries_folder_validates_exists(mock_kee, tmp_path):
    """
    Regression test: queries_folder must exist

    If path doesn't exist, should use default.
    """
    nonexistent = tmp_path / "nonexistent"

    def mock_find_entries(title, group, first):
        entry = Mock()
        if title == "QUERIES_FOLDER":
            entry.username = str(nonexistent)  # Doesn't exist
        else:
            return None
        return entry

    mock_kee.db.find_entries = mock_find_entries

    settings = Settings(mock_kee)

    # Should use DEFAULT_QUERIES_FOLDER
    assert settings.queries_folder == DEFAULT_QUERIES_FOLDER


@pytest.mark.regression
def test_logs_folder_validates_exists(mock_kee, tmp_path):
    """logs_folder must be valid directory"""
    nonexistent = tmp_path / "nonexistent"

    def mock_find_entries(title, group, first):
        entry = Mock()
        if title == "LOGS_FOLDER":
            entry.username = str(nonexistent)
        else:
            return None
        return entry

    mock_kee.db.find_entries = mock_find_entries

    settings = Settings(mock_kee)

    # Should use default
    assert settings.logs_folder == DEFAULT_LOGS_FOLDER


# ================================================================================
# Folder Validation
# ================================================================================


@pytest.mark.unit
def test_validate_folder_valid(mock_kee, tmp_path):
    """Should accept an existing directory"""
    settings = Settings(mock_kee)

    assert settings._validate_folder(tmp_path) is True


@pytest.mark.unit
def test_validate_folder_invalid_raises_error(mock_kee, tmp_path):
    """Should reject a path that doesn't exist"""
    settings = Settings(mock_kee)
    nonexistent = tmp_path / "nonexistent"

    with pytest.raises(ValueError, match="Folder doesn't exist"):
        settings._validate_folder(nonexistent)


@pytest.mark.unit
def test_save_queries_folder_invalid_raises_error(mock_kee, tmp_path):
    """save() should raise if queries_folder doesn't exist, regardless of logs_are_on"""
    mock_entry = Mock()
    mock_entry.username = ""
    mock_kee.db.find_entries = Mock(return_value=mock_entry)

    settings = Settings(mock_kee)
    settings.queries_folder = tmp_path / "nonexistent"

    with pytest.raises(ValueError, match="Folder doesn't exist"):
        settings.save()


@pytest.mark.unit
def test_save_logs_folder_invalid_raises_error_when_logs_are_on(mock_kee, tmp_path):
    """save() should raise if logs_folder doesn't exist while logs_are_on is True"""
    mock_entry = Mock()
    mock_entry.username = ""
    mock_kee.db.find_entries = Mock(return_value=mock_entry)

    settings = Settings(mock_kee)
    settings.queries_folder = tmp_path  # valid - isolates the assertion to logs_folder
    settings.logs_are_on = True
    settings.logs_folder = tmp_path / "nonexistent"

    with pytest.raises(ValueError, match="Folder doesn't exist"):
        settings.save()


@pytest.mark.unit
def test_save_logs_folder_invalid_does_not_raise_when_logs_are_off(mock_kee, tmp_path):
    """An invalid logs_folder must not block saving while logs are disabled - it has no runtime
    effect in that case."""
    mock_entry = Mock()
    mock_entry.username = ""
    mock_kee.db.find_entries = Mock(return_value=mock_entry)

    settings = Settings(mock_kee)
    settings.queries_folder = tmp_path  # valid
    settings.logs_are_on = False
    settings.logs_folder = tmp_path / "nonexistent"

    assert settings.save() is True


# ================================================================================
# Load/Save Functionality
# ================================================================================


@pytest.mark.unit
def test_load_reads_from_keepass(mock_kee):
    """load() should read settings from KeePass database"""

    def mock_find_entries(title, group, first):
        entry = Mock()
        if title == "SETTINGS_VERSION":
            entry.username = "12"
        else:
            return None
        return entry

    mock_kee.db.find_entries = mock_find_entries

    settings = Settings(mock_kee)

    # Verify loaded values
    assert settings.settings_version == "12"


@pytest.mark.unit
def test_save_writes_to_keepass(mock_kee, tmp_path):
    """save() should write settings to KeePass database"""
    # Mock find_entries to return existing entries
    mock_entry = Mock()
    mock_entry.username = ""
    mock_kee.db.find_entries = Mock(return_value=mock_entry)

    settings = Settings(mock_kee)

    # Change a value
    settings.settings_version = "12"
    settings.queries_folder = tmp_path  # must exist - save() now validates it regardless of what changed

    # Save
    result = settings.save()

    # Verify
    assert result is True
    assert mock_kee.save_db.called


@pytest.mark.unit
def test_save_creates_missing_entries(mock_kee, tmp_path):
    """save() should create entries that don't exist"""
    # Mock find_entries to return None (entry doesn't exist)
    mock_kee.db.find_entries = Mock(return_value=None)

    # Mock add_entry
    new_entry = Mock()
    new_entry.username = ""
    mock_kee.db.add_entry = Mock(return_value=new_entry)

    settings = Settings(mock_kee)
    settings.queries_folder = tmp_path  # must exist - save() now validates it regardless of what changed

    # Save
    settings.save()

    # Verify add_entry was called
    assert mock_kee.db.add_entry.called


# ================================================================================
# JWT Secret
# ================================================================================


@pytest.mark.unit
def test_jwt_secret_generated_when_missing(mock_kee):
    """load() should generate a jwt_secret when none exists in KeePass yet"""
    settings = Settings(mock_kee)

    assert settings.jwt_secret
    assert len(settings.jwt_secret) >= 32


@pytest.mark.unit
def test_jwt_secret_generation_is_persisted(mock_kee):
    """A freshly generated jwt_secret must be saved immediately, not just kept in memory -
    otherwise a new one would be generated (and all existing tokens invalidated) on every
    server restart."""
    settings = Settings(mock_kee)

    assert mock_kee.save_db.called


@pytest.mark.unit
def test_jwt_secret_not_regenerated_when_present(mock_kee):
    def mock_find_entries(title, group, first):
        entry = Mock()
        entry.username = "existing-secret-value" if title == "JWT_SECRET" else ""
        return entry

    mock_kee.db.find_entries = mock_find_entries

    settings = Settings(mock_kee)

    assert settings.jwt_secret == "existing-secret-value"
    assert not mock_kee.save_db.called


# ================================================================================
# Boolean Conversion (LOGS_ARE_ON)
# ================================================================================


@pytest.mark.unit
def test_logs_are_on_true_from_string(mock_kee):
    """LOGS_ARE_ON should convert 'true' string to boolean"""

    def mock_find_entries(title, group, first):
        entry = Mock()
        if title == "LOGS_ARE_ON":
            entry.username = "true"
        else:
            return None
        return entry

    mock_kee.db.find_entries = mock_find_entries

    settings = Settings(mock_kee)

    assert settings.logs_are_on is True


@pytest.mark.unit
def test_logs_are_on_false_from_string(mock_kee):
    """Any non-'true' value should become False"""

    def mock_find_entries(title, group, first):
        entry = Mock()
        if title == "LOGS_ARE_ON":
            entry.username = "false"
        else:
            return None
        return entry

    mock_kee.db.find_entries = mock_find_entries

    settings = Settings(mock_kee)

    assert settings.logs_are_on is False


# ================================================================================
# Version Control
# ================================================================================


@pytest.mark.unit
def test_ctrl_version_exact_match(mock_kee):
    """ctrl_version should return True for exact match"""
    settings = Settings(mock_kee)

    assert settings.ctrl_version("1.2.3", "1.2.3") is True


@pytest.mark.unit
def test_ctrl_version_newer(mock_kee):
    """ctrl_version should return True if current > minimum"""
    settings = Settings(mock_kee)

    assert settings.ctrl_version("1.3.0", "1.2.3") is True
    assert settings.ctrl_version("2.0.0", "1.9.9") is True


@pytest.mark.unit
def test_ctrl_version_older(mock_kee):
    """ctrl_version should return False if current < minimum"""
    settings = Settings(mock_kee)

    assert settings.ctrl_version("1.2.3", "1.2.4") is False
    assert settings.ctrl_version("1.0.0", "2.0.0") is False


@pytest.mark.unit
def test_ctrl_version_different_length(mock_kee):
    """ctrl_version should compare missing segments as 0, not fail"""
    settings = Settings(mock_kee)

    assert settings.ctrl_version("1.2", "1.2.3") is False
    assert settings.ctrl_version("1.2.3", "1.2") is True


@pytest.mark.unit
def test_ctrl_version_invalid_format_returns_true(mock_kee):
    """Invalid version format should not block (returns True)"""
    settings = Settings(mock_kee)

    # Should not raise error, returns True
    assert settings.ctrl_version("invalid", "1.2.3") is True


@pytest.mark.unit
def test_get_min_version_reads_json(mock_kee, tmp_path):
    """get_min_version should read version from _version_min.json"""
    settings = Settings(mock_kee)

    # Create version file
    version_file = tmp_path / "_version_min.json"
    version_data = {"application": "1.5.0", "settings": "3"}
    with open(version_file, "w", encoding="utf-8") as f:
        json.dump(version_data, f)

    app_ver, settings_ver, retrieve_failed = settings.get_min_version(tmp_path)

    assert app_ver == "1.5.0"
    assert settings_ver == "3"
    assert retrieve_failed is False


@pytest.mark.unit
def test_get_min_version_no_file_returns_defaults(mock_kee, tmp_path):
    """get_min_version without file should return default versions"""
    settings = Settings(mock_kee)

    app_ver, settings_ver, retrieve_failed = settings.get_min_version(tmp_path)

    assert app_ver == "0.0.0"
    assert settings_ver == "0"


@pytest.mark.unit
def test_get_min_version_nonexistent_folder_raises_error(mock_kee):
    """get_min_version with invalid folder should raise ValueError"""
    settings = Settings(mock_kee)

    with pytest.raises(ValueError, match="doesn't exist"):
        settings.get_min_version("/nonexistent/folder")


# ================================================================================
# Reload Functionality
# ================================================================================


@pytest.mark.unit
def test_reload_calls_load_with_reload_true(mock_kee):
    """reload() should call load(reload=True)"""
    settings = Settings(mock_kee)

    # Mock load to track if called
    with patch.object(settings, "load") as mock_load:
        settings.reload()

        # Verify load was called with reload=True
        mock_load.assert_called_once_with(reload=True)


# ================================================================================
# Error Handling
# ================================================================================


@pytest.mark.unit
def test_load_handles_db_open_failure(mock_kee):
    """load() should handle database open failure gracefully, keeping __init__ defaults"""
    mock_kee.is_ko = True  # Database failed to open

    settings = Settings(mock_kee)

    # Should not raise error
    # Settings should keep the __init__ defaults, since load() bailed out early
    assert settings.settings_version == ""
    assert settings.queries_folder == DEFAULT_QUERIES_FOLDER
