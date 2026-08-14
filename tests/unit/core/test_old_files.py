"""
Tests for Pytre/old_files.py

Tests for file cleanup functionality:
- Old files identification based on modification age (age_in_days threshold)
- File deletion functionality
- Day-after-most-recent-modification calculation
"""

import os
import pytest
from pathlib import Path
from datetime import datetime
from dateutil.relativedelta import relativedelta

from Pytre_Core.old_files import list_old_files, delete_files, day_after_most_recent


def _set_age_in_days(file: Path, days: int) -> None:
    """Set a file's modification time to `days` days in the past"""
    ts = (datetime.now() - relativedelta(days=days)).timestamp()
    os.utime(file, (ts, ts))


# ================================================================================
# list_old_files Functionality
# ================================================================================


@pytest.mark.unit
def test_list_old_files_returns_empty_for_nonexistent_folder():
    """list_old_files should return empty list for nonexistent folder"""
    nonexistent = Path("/nonexistent/folder")

    result = list_old_files(nonexistent)

    assert result == []


@pytest.mark.unit
def test_list_old_files_returns_empty_for_empty_folder(tmp_path):
    """list_old_files should return empty list for an empty folder"""
    folder = tmp_path / "empty"
    folder.mkdir()

    result = list_old_files(folder)

    assert result == []


@pytest.mark.unit
def test_list_old_files_excludes_recent_files(tmp_path):
    """Files younger than age_in_days should not be listed"""
    folder = tmp_path / "recent_only"
    folder.mkdir()

    recent = folder / "recent.txt"
    recent.write_text("content")
    _set_age_in_days(recent, 5)

    result = list_old_files(folder, age_in_days=10)

    assert result == []


@pytest.mark.unit
def test_list_old_files_includes_files_older_than_threshold(tmp_path):
    """Files older than age_in_days should be listed, younger ones should not"""
    folder = tmp_path / "mixed"
    folder.mkdir()

    old_file = folder / "old.txt"
    old_file.write_text("content")
    _set_age_in_days(old_file, 15)

    recent_file = folder / "recent.txt"
    recent_file.write_text("content")
    _set_age_in_days(recent_file, 2)

    result = list_old_files(folder, age_in_days=10)
    result_names = [f.name for f in result]

    assert "old.txt" in result_names
    assert "recent.txt" not in result_names


@pytest.mark.unit
def test_list_old_files_respects_custom_age_threshold(tmp_path):
    """age_in_days parameter should change the cutoff"""
    folder = tmp_path / "custom_threshold"
    folder.mkdir()

    file = folder / "file.txt"
    file.write_text("content")
    _set_age_in_days(file, 5)

    # With a 3-day threshold, a 5-day-old file should be listed
    assert len(list_old_files(folder, age_in_days=3)) == 1

    # With a 10-day threshold, the same file should NOT be listed
    assert list_old_files(folder, age_in_days=10) == []


# ================================================================================
# delete_files Functionality
# ================================================================================


@pytest.mark.unit
def test_delete_files_removes_files(tmp_path):
    """delete_files should delete specified files"""
    folder = tmp_path / "delete_test"
    folder.mkdir()

    file1 = folder / "file1.txt"
    file1.write_text("content1")

    file2 = folder / "file2.txt"
    file2.write_text("content2")

    delete_files([file1, file2])

    assert not file1.exists()
    assert not file2.exists()


@pytest.mark.unit
def test_delete_files_handles_missing_files():
    """delete_files should handle missing files gracefully"""
    nonexistent1 = Path("/nonexistent/file1.txt")
    nonexistent2 = Path("/nonexistent/file2.txt")

    # Should not raise error
    delete_files([nonexistent1, nonexistent2])


@pytest.mark.unit
def test_delete_files_empty_list():
    """delete_files should handle empty list"""
    # Should not raise error
    delete_files([])


@pytest.mark.unit
def test_delete_files_skips_directories(tmp_path):
    """delete_files should skip directories rather than erroring"""
    folder = tmp_path / "with_subdir"
    folder.mkdir()
    subdir = folder / "subdir"
    subdir.mkdir()

    # Should not raise, and should not remove the directory
    delete_files([subdir])

    assert subdir.exists()


# ================================================================================
# day_after_most_recent Functionality
# ================================================================================


@pytest.mark.unit
def test_day_after_most_recent_single_file(tmp_path):
    """day_after_most_recent should return the file's modification date + 1 day"""
    file1 = tmp_path / "file1.txt"
    file1.write_text("content")

    result = day_after_most_recent([file1])

    expected = datetime.fromtimestamp(file1.stat().st_mtime) + relativedelta(days=1)
    assert result.date() == expected.date()


@pytest.mark.unit
def test_day_after_most_recent_returns_max_date_plus_one_day(tmp_path):
    """day_after_most_recent should use the most recently modified file"""
    file1 = tmp_path / "file1.txt"
    file1.write_text("content")
    _set_age_in_days(file1, 2)

    file2 = tmp_path / "file2.txt"
    file2.write_text("content")
    _set_age_in_days(file2, 1)  # most recent

    file3 = tmp_path / "file3.txt"
    file3.write_text("content")
    _set_age_in_days(file3, 3)

    result = day_after_most_recent([file1, file2, file3])

    expected = datetime.fromtimestamp(file2.stat().st_mtime) + relativedelta(days=1)
    assert result.date() == expected.date()


@pytest.mark.unit
def test_day_after_most_recent_empty_list_raises_error():
    """day_after_most_recent should raise ValueError for an empty list"""
    with pytest.raises(ValueError, match="empty list"):
        day_after_most_recent([])


# ================================================================================
# Integration
# ================================================================================


@pytest.mark.unit
def test_full_cleanup_workflow(tmp_path):
    """Integration test: identify old files, get their date, then delete them"""
    folder = tmp_path / "workflow"
    folder.mkdir()

    old_file = folder / "old.txt"
    old_file.write_text("old")
    _set_age_in_days(old_file, 15)

    recent_file = folder / "recent.txt"
    recent_file.write_text("recent")
    _set_age_in_days(recent_file, 1)

    old_files = list_old_files(folder, age_in_days=10)
    assert len(old_files) == 1

    recent_date = day_after_most_recent(old_files)
    assert isinstance(recent_date, datetime)

    delete_files(old_files)

    assert not old_file.exists()
    assert recent_file.exists()
