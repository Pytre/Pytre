"""
Tests for Pytre/utils.py

REGRESSION TESTS:
- Path validation to prevent subprocess injection (CRITICAL SECURITY FIX)
"""

import pytest
import os
from pathlib import Path
from unittest.mock import patch, mock_open

from Pytre_Core.utils import validate_path, startfile, showfile


# ================================================================================
# REGRESSION TESTS - Path Validation Security
# ================================================================================


@pytest.mark.security
@pytest.mark.regression
def test_validate_path_rejects_nonexistent(tmp_path):
    """
    Regression test: validate before subprocess

    CRITICAL: Must reject non-existent paths to prevent injection attacks.
    """
    nonexistent = tmp_path / "does_not_exist.txt"

    with pytest.raises(FileNotFoundError):
        validate_path(nonexistent)


@pytest.mark.security
@pytest.mark.regression
def test_validate_path_resolves_symlinks(tmp_path):
    """
    Regression test: must resolve symlinks

    CRITICAL: Must resolve symlinks to prevent path traversal attacks.
    """
    # Create real file
    real_file = tmp_path / "real.txt"
    real_file.write_text("content")

    # Create symlink to it
    symlink = tmp_path / "link.txt"
    symlink.symlink_to(real_file)

    # Test: Should resolve to absolute path
    result = validate_path(symlink)

    # Verify
    assert result.is_absolute()
    assert result.exists()
    # Should resolve to real file location
    assert result.resolve() == real_file.resolve()


@pytest.mark.security
@pytest.mark.regression
def test_validate_path_checks_permissions(tmp_path):
    """
    Regression test: must verify read permissions

    CRITICAL: Must check file is readable before passing to subprocess.
    """
    file_path = tmp_path / "test.txt"
    file_path.write_text("content")

    # Remove all permissions
    file_path.chmod(0o000)

    try:
        with pytest.raises(PermissionError):
            validate_path(file_path)
    finally:
        # Cleanup - restore permissions so pytest can delete
        file_path.chmod(0o644)


@pytest.mark.security
def test_validate_path_rejects_empty_string():
    """Empty filename should raise ValueError"""
    with pytest.raises(ValueError, match="cannot be empty"):
        validate_path("")


@pytest.mark.security
def test_validate_path_rejects_none():
    """None filename should raise ValueError"""
    with pytest.raises(ValueError, match="cannot be empty"):
        validate_path(None)  # type: ignore


@pytest.mark.security
def test_validate_path_accepts_valid_file(tmp_path):
    """Valid file should pass validation"""
    valid_file = tmp_path / "valid.txt"
    valid_file.write_text("content")

    result = validate_path(valid_file)

    assert result.is_absolute()
    assert result.exists()
    assert result.is_file()


@pytest.mark.security
def test_validate_path_accepts_valid_directory(tmp_path):
    """Valid directory should pass validation"""
    valid_dir = tmp_path / "valid_dir"
    valid_dir.mkdir()

    result = validate_path(valid_dir)

    assert result.is_absolute()
    assert result.exists()
    assert result.is_dir()


@pytest.mark.security
def test_validate_path_handles_relative_paths(tmp_path):
    """Relative paths should be resolved to absolute"""
    # Create file in temp directory
    test_file = tmp_path / "test.txt"
    test_file.write_text("content")

    # Change to temp directory
    original_dir = Path.cwd()
    try:
        os.chdir(tmp_path)

        # Test with relative path
        result = validate_path("test.txt")

        # Should resolve to absolute
        assert result.is_absolute()
        assert result.exists()
    finally:
        # Restore original directory
        os.chdir(original_dir)


# ================================================================================
# Subprocess Security - startfile and showfile
# ================================================================================


@pytest.mark.security
def test_startfile_validates_path_before_subprocess(tmp_path):
    """startfile must validate path before calling subprocess"""
    nonexistent = tmp_path / "nonexistent.txt"

    # Should raise FileNotFoundError during validation
    # NOT during subprocess call
    with pytest.raises(FileNotFoundError):
        startfile(nonexistent)


@pytest.mark.security
def test_showfile_validates_path_before_subprocess(tmp_path):
    """showfile must validate path before calling subprocess"""
    nonexistent = tmp_path / "nonexistent.txt"

    # Should raise FileNotFoundError during validation
    # NOT during subprocess call
    with pytest.raises(FileNotFoundError):
        showfile(nonexistent)


@pytest.mark.security
def test_startfile_accepts_str_or_path(tmp_path):
    """startfile should accept both str and Path types"""
    test_file = tmp_path / "test.txt"
    test_file.write_text("content")

    # Mock subprocess and get_file_manager to avoid actually opening file
    with patch('Pytre_Core.utils.subprocess.Popen') as mock_popen, \
         patch('Pytre_Core.utils.get_file_manager', return_value=None):
        # Test with Path
        startfile(test_file)
        assert mock_popen.called

        # Reset mock count
        mock_popen.reset_mock()

        # Test with str
        startfile(str(test_file))
        assert mock_popen.call_count == 1


@pytest.mark.security
def test_showfile_accepts_str_or_path(tmp_path):
    """showfile should accept both str and Path types"""
    test_file = tmp_path / "test.txt"
    test_file.write_text("content")

    # Mock both subprocess and get_file_manager to avoid side effects
    with patch('Pytre_Core.utils.subprocess.Popen') as mock_popen, \
         patch('Pytre_Core.utils.get_file_manager', return_value='nautilus'):
        # Test with Path
        showfile(test_file)
        assert mock_popen.called

        # Reset mock count
        mock_popen.reset_mock()

        # Test with str
        showfile(str(test_file))
        assert mock_popen.call_count == 1


# ================================================================================
# Edge Cases
# ================================================================================


@pytest.mark.security
def test_validate_path_with_special_characters(tmp_path):
    """Path with special characters should be handled safely"""
    special_file = tmp_path / "file with spaces & special!.txt"
    special_file.write_text("content")

    result = validate_path(special_file)

    assert result.exists()
    assert result.is_absolute()


@pytest.mark.security
def test_validate_path_with_unicode(tmp_path):
    """Path with unicode characters should be handled safely"""
    unicode_file = tmp_path / "fichier_éàç_中文.txt"
    unicode_file.write_text("content")

    result = validate_path(unicode_file)

    assert result.exists()
    assert result.is_absolute()


@pytest.mark.security
def test_validate_path_rejects_special_devices():
    """Special device files should be rejected"""
    # /dev/null is not a regular file or directory
    # Note: This test may behave differently on Windows
    if os.name == 'posix':  # Unix-like systems
        with pytest.raises(ValueError, match="not a regular file or directory"):
            validate_path("/dev/null")


# ================================================================================
# Platform Detection Tests
# ================================================================================


@pytest.mark.unit
def test_get_system_returns_platform():
    """get_system should return current platform"""
    from Pytre_Core.utils import get_system

    result = get_system()

    # Should return one of the known platforms
    assert result in ["Windows", "Linux", "Darwin"]


# ================================================================================
# File Manager Detection Tests
# ================================================================================


@pytest.mark.unit
def test_get_file_manager_windows():
    """get_file_manager should return explorer on Windows"""
    from Pytre_Core.utils import get_file_manager

    with patch("platform.system", return_value="Windows"):
        result = get_file_manager()
        assert result == "explorer"


@pytest.mark.unit
def test_get_file_manager_darwin():
    """get_file_manager should return open on macOS"""
    from Pytre_Core.utils import get_file_manager

    with patch("platform.system", return_value="Darwin"):
        result = get_file_manager()
        assert result == "open"


@pytest.mark.unit
def test_get_file_manager_linux():
    """get_file_manager should call _linux_file_manager on Linux"""
    from Pytre_Core.utils import get_file_manager

    with patch("platform.system", return_value="Linux"):
        with patch("Pytre_Core.utils._linux_file_manager", return_value="nautilus"):
            result = get_file_manager()
            assert result == "nautilus"


@pytest.mark.unit
def test_get_file_manager_unsupported_os():
    """get_file_manager should raise ValueError for unsupported OS"""
    from Pytre_Core.utils import get_file_manager

    with patch("platform.system", return_value="FreeBSD"):
        with pytest.raises(ValueError, match="not supported"):
            get_file_manager()


# ================================================================================
# Linux File Manager Detection Tests
# ================================================================================


@pytest.mark.unit
def test_linux_file_manager_handles_xdg_failure():
    """_linux_file_manager should return empty string if xdg-mime fails"""
    from Pytre_Core.utils import _linux_file_manager
    from subprocess import CalledProcessError

    with patch("subprocess.run", side_effect=CalledProcessError(1, "xdg-mime")):
        result = _linux_file_manager()
        assert result == ""


@pytest.mark.unit
def test_linux_file_manager_handles_empty_desktop_entry():
    """_linux_file_manager should return empty string if desktop entry is empty"""
    from Pytre_Core.utils import _linux_file_manager

    mock_result = type('obj', (), {'stdout': '  \n'})()

    with patch("subprocess.run", return_value=mock_result):
        result = _linux_file_manager()
        assert result == ""


@pytest.mark.unit
def test_linux_file_manager_handles_missing_desktop_file():
    """_linux_file_manager should return empty string if desktop file doesn't exist"""
    from Pytre_Core.utils import _linux_file_manager

    mock_result = type('obj', (), {'stdout': 'nautilus.desktop\n'})()

    with patch("subprocess.run", return_value=mock_result):
        # Mock Path.exists to return False
        with patch("pathlib.Path.exists", return_value=False):
            result = _linux_file_manager()
            assert result == ""


@pytest.mark.unit
def test_linux_file_manager_parses_desktop_entry(tmp_path):
    """_linux_file_manager should parse Exec line from .desktop file"""
    from Pytre_Core.utils import _linux_file_manager

    # Create mock .desktop file in .local/share/applications
    local_apps = tmp_path / ".local" / "share" / "applications"
    local_apps.mkdir(parents=True)

    desktop_file = local_apps / "nautilus.desktop"
    desktop_content = """[Desktop Entry]
Name=Files
Exec=nautilus %U
Icon=system-file-manager
Type=Application
"""
    desktop_file.write_text(desktop_content)

    mock_result = type('obj', (), {'stdout': 'nautilus.desktop\n'})()

    with patch("subprocess.run", return_value=mock_result):
        # Mock Path.home to return tmp_path
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = _linux_file_manager()
            assert result == "nautilus"


# ================================================================================
# startfile() Platform Branch Tests
# ================================================================================


@pytest.mark.unit
@pytest.mark.skipif(os.name != 'nt', reason="os.startfile only exists on Windows")
def test_startfile_windows_calls_os_startfile(tmp_path):
    """startfile should call os.startfile on Windows"""
    test_file = tmp_path / "test.txt"
    test_file.write_text("content")

    with patch("platform.system", return_value="Windows"):
        with patch("os.startfile") as mock_startfile:
            startfile(test_file)

            mock_startfile.assert_called_once()
            assert str(test_file) in str(mock_startfile.call_args)


@pytest.mark.unit
def test_startfile_darwin_uses_open_command(tmp_path):
    """startfile should use 'open' command on macOS"""
    test_file = tmp_path / "test.txt"
    test_file.write_text("content")

    with patch("platform.system", return_value="Darwin"):
        with patch("subprocess.Popen") as mock_popen:
            startfile(test_file)

            mock_popen.assert_called_once()
            args = mock_popen.call_args[0][0]
            assert args[0] == "open"
            assert str(test_file) in args


@pytest.mark.unit
def test_startfile_linux_file_uses_xdg_open(tmp_path):
    """startfile should use xdg-open for files on Linux"""
    test_file = tmp_path / "test.txt"
    test_file.write_text("content")

    with patch("platform.system", return_value="Linux"):
        with patch("Pytre_Core.utils.get_file_manager", return_value=None):
            with patch("subprocess.Popen") as mock_popen:
                startfile(test_file)

                mock_popen.assert_called_once()
                args = mock_popen.call_args[0][0]
                assert "xdg-open" in args


@pytest.mark.unit
def test_startfile_linux_directory_uses_file_manager(tmp_path):
    """startfile should use file manager with --select for directories on Linux"""
    test_dir = tmp_path / "test_dir"
    test_dir.mkdir()

    with patch("platform.system", return_value="Linux"):
        with patch("Pytre_Core.utils.get_file_manager", return_value="nautilus"):
            with patch("subprocess.Popen") as mock_popen:
                startfile(test_dir)

                mock_popen.assert_called_once()
                args = mock_popen.call_args[0][0]
                assert "nautilus" in args
                assert "--select" in args


@pytest.mark.unit
def test_startfile_unsupported_os_raises_error(tmp_path):
    """startfile should raise ValueError for unsupported OS"""
    test_file = tmp_path / "test.txt"
    test_file.write_text("content")

    with patch("platform.system", return_value="FreeBSD"):
        with pytest.raises(ValueError, match="not supported"):
            startfile(test_file)


# ================================================================================
# showfile() Platform Branch Tests
# ================================================================================


@pytest.mark.unit
def test_showfile_without_file_manager_calls_startfile(tmp_path):
    """showfile should fall back to startfile if no file manager"""
    test_file = tmp_path / "test.txt"
    test_file.write_text("content")

    with patch("Pytre_Core.utils.get_file_manager", return_value=None):
        with patch("Pytre_Core.utils.startfile") as mock_startfile:
            showfile(test_file)

            # Should call startfile with parent folder
            mock_startfile.assert_called_once()


@pytest.mark.unit
def test_showfile_windows_uses_select_flag(tmp_path):
    """showfile should use /select flag on Windows"""
    test_file = tmp_path / "test.txt"
    test_file.write_text("content")

    with patch("platform.system", return_value="Windows"):
        with patch("Pytre_Core.utils.get_file_manager", return_value="explorer"):
            with patch("subprocess.Popen") as mock_popen:
                showfile(test_file)

                mock_popen.assert_called_once()
                args = mock_popen.call_args[0][0]
                assert "explorer" in args
                assert "/select," in args


@pytest.mark.unit
def test_showfile_linux_uses_select_flag(tmp_path):
    """showfile should use --select flag on Linux"""
    test_file = tmp_path / "test.txt"
    test_file.write_text("content")

    with patch("platform.system", return_value="Linux"):
        with patch("Pytre_Core.utils.get_file_manager", return_value="nautilus"):
            with patch("subprocess.Popen") as mock_popen:
                showfile(test_file)

                mock_popen.assert_called_once()
                args = mock_popen.call_args[0][0]
                assert "nautilus" in args
                assert "--select" in args


@pytest.mark.unit
def test_showfile_darwin_uses_reveal_flag(tmp_path):
    """showfile should use -R flag on macOS"""
    test_file = tmp_path / "test.txt"
    test_file.write_text("content")

    with patch("platform.system", return_value="Darwin"):
        with patch("Pytre_Core.utils.get_file_manager", return_value="open"):
            with patch("subprocess.Popen") as mock_popen:
                showfile(test_file)

                mock_popen.assert_called_once()
                args = mock_popen.call_args[0][0]
                assert "open" in args
                assert "-R" in args
