"""
Tests for Pytre/settings.py

REGRESSION TESTS:
- Issue 2.1: Input validation for field_separator, decimal_separator, paths
- Issue 3.2: Kee initialized in __init__ (not at class level)
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
    mock._open_db = Mock(return_value=True)
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
# REGRESSION TESTS - Issue 3.2: Dependency Injection
# ================================================================================


@pytest.mark.regression
def test_kee_injected_via_constructor(mock_kee):
    """
    Regression test for Issue 3.2 - Kee is injected via the constructor

    Settings no longer constructs its own Kee (removed with the singleton
    pattern) - the caller (AppContext) builds and passes it in.
    """
    settings = Settings(mock_kee)

    assert settings.kee is mock_kee


# ================================================================================
# REGRESSION TESTS - Issue 2.1: Input Validation
# ================================================================================


@pytest.mark.regression
def test_field_separator_validates_length(mock_kee):
    """
    Regression test for Issue 2.1 - field_separator must be single char

    If invalid, should use default ';'
    """
    entry = Mock()
    entry.username = ";;"  # Invalid: 2 characters
    mock_kee.db.find_entries = Mock(return_value=entry)

    settings = Settings(mock_kee)

    # Should use default ';'
    assert settings.field_separator == ";"
    assert len(settings.field_separator) == 1


@pytest.mark.regression
def test_field_separator_empty_uses_default(mock_kee):
    """Empty field_separator should use default"""
    entry = Mock()
    entry.username = ""  # Empty
    mock_kee.db.find_entries = Mock(return_value=entry)

    settings = Settings(mock_kee)

    assert settings.field_separator == ";"


@pytest.mark.regression
def test_decimal_separator_validates_length(mock_kee):
    """
    Regression test for Issue 2.1 - decimal_separator must be single char
    """

    def mock_find_entries(title, group, first):
        entry = Mock()
        if title == "FIELD_SEPARATOR":
            entry.username = ";"
        elif title == "DECIMAL_SEPARATOR":
            entry.username = ",,"  # Invalid: 2 characters
        else:
            return None
        return entry

    mock_kee.db.find_entries = mock_find_entries

    settings = Settings(mock_kee)

    # Should use default ','
    assert settings.decimal_separator == ","


@pytest.mark.regression
def test_decimal_separator_cannot_equal_field_separator(mock_kee):
    """
    Regression test for Issue 2.1 - separators must be different
    """

    def mock_find_entries(title, group, first):
        entry = Mock()
        if title == "FIELD_SEPARATOR":
            entry.username = ";"
        elif title == "DECIMAL_SEPARATOR":
            entry.username = ";"  # Same as field separator!
        else:
            return None
        return entry

    mock_kee.db.find_entries = mock_find_entries

    settings = Settings(mock_kee)

    # Should use default ',' instead
    assert settings.decimal_separator == ","
    assert settings.decimal_separator != settings.field_separator


@pytest.mark.regression
def test_queries_folder_validates_exists(mock_kee, tmp_path):
    """
    Regression test for Issue 2.1 - queries_folder must exist

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
# Encoding Validation
# ================================================================================


@pytest.mark.unit
def test_validate_encoding_format_valid(mock_kee):
    """Should accept valid encodings"""
    settings = Settings(mock_kee)

    # Should not raise error
    assert settings._validate_encoding_format("utf-8") is True
    assert settings._validate_encoding_format("utf-8-sig") is True
    assert settings._validate_encoding_format("latin-1") is True


@pytest.mark.unit
def test_validate_encoding_format_invalid_raises_error(mock_kee):
    """Should reject invalid encodings"""
    settings = Settings(mock_kee)

    with pytest.raises(LookupError, match="Unknown encoding"):
        settings._validate_encoding_format("invalid-encoding-xyz")


@pytest.mark.unit
def test_extract_encoding_invalid_uses_default(mock_kee):
    """Invalid extract_encoding should use default"""

    def mock_find_entries(title, group, first):
        entry = Mock()
        if title == "EXTRACT_ENCODING":
            entry.username = "invalid-encoding"
        else:
            return None
        return entry

    mock_kee.db.find_entries = mock_find_entries

    settings = Settings(mock_kee)

    # Should use default
    assert settings.extract_encoding == "utf-8-sig"


# ================================================================================
# Load/Save Functionality
# ================================================================================


@pytest.mark.unit
def test_load_reads_from_keepass(mock_kee):
    """load() should read settings from KeePass database"""

    def mock_find_entries(title, group, first):
        entry = Mock()
        if title == "FIELD_SEPARATOR":
            entry.username = "|"
        elif title == "DECIMAL_SEPARATOR":
            entry.username = "."
        elif title == "DATE_FORMAT":
            entry.username = "yyyy-MM-dd"
        else:
            return None
        return entry

    mock_kee.db.find_entries = mock_find_entries

    settings = Settings(mock_kee)

    # Verify loaded values
    assert settings.field_separator == "|"
    assert settings.decimal_separator == "."
    assert settings.date_format == "yyyy-MM-dd"


@pytest.mark.unit
def test_save_writes_to_keepass(mock_kee):
    """save() should write settings to KeePass database"""
    # Mock find_entries to return existing entries
    mock_entry = Mock()
    mock_entry.username = ""
    mock_kee.db.find_entries = Mock(return_value=mock_entry)

    settings = Settings(mock_kee)

    # Change a value
    settings.field_separator = "|"

    # Save
    result = settings.save()

    # Verify
    assert result is True
    assert mock_kee.save_db.called


@pytest.mark.unit
def test_save_creates_missing_entries(mock_kee):
    """save() should create entries that don't exist"""
    # Mock find_entries to return None (entry doesn't exist)
    mock_kee.db.find_entries = Mock(return_value=None)

    # Mock add_entry
    new_entry = Mock()
    new_entry.username = ""
    mock_kee.db.add_entry = Mock(return_value=new_entry)

    settings = Settings(mock_kee)
    settings.field_separator = ";"

    # Save
    settings.save()

    # Verify add_entry was called
    assert mock_kee.db.add_entry.called


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
    assert settings.field_separator == ";"


@pytest.mark.unit
def test_date_format_empty_uses_default(mock_kee):
    """Empty date format should use default dd/MM/yyyy"""

    def mock_find_entries(title, group, first):
        entry = Mock()
        if title == "DATE_FORMAT":
            entry.username = ""
        else:
            return None
        return entry

    mock_kee.db.find_entries = mock_find_entries

    settings = Settings(mock_kee)

    assert settings.date_format == "dd/MM/yyyy"
