"""
Tests for Pytre_Client/save_as.py
"""

from unittest.mock import Mock

import pytest

from Pytre_Client.save_as import save_as
from Pytre_Core.user_prefs import PrefsEnum


@pytest.fixture
def user_prefs():
    prefs = Mock()
    prefs.get.return_value = "/some/initial/dir"
    return prefs


@pytest.mark.unit
def test_returns_none_when_src_file_is_falsy(user_prefs, monkeypatch):
    asksaveasfilename = Mock()
    monkeypatch.setattr("Pytre_Client.save_as.filedialog.asksaveasfilename", asksaveasfilename)

    result = save_as(user_prefs, None, "")

    assert result is None
    assert not asksaveasfilename.called


@pytest.mark.unit
def test_shows_error_and_returns_none_when_src_file_does_not_exist(user_prefs, monkeypatch, tmp_path):
    showerror = Mock()
    asksaveasfilename = Mock()
    monkeypatch.setattr("Pytre_Client.save_as.messagebox.showerror", showerror)
    monkeypatch.setattr("Pytre_Client.save_as.filedialog.asksaveasfilename", asksaveasfilename)

    result = save_as(user_prefs, None, tmp_path / "does_not_exist.csv")

    assert result is None
    assert showerror.called
    assert not asksaveasfilename.called


@pytest.mark.unit
def test_returns_none_without_writing_when_dialog_is_cancelled(user_prefs, monkeypatch, tmp_path):
    """asksaveasfilename returns "" (Tkinter's convention) when the user cancels the dialog."""
    src_file = tmp_path / "source.csv"
    src_file.write_text("data")
    monkeypatch.setattr("Pytre_Client.save_as.filedialog.asksaveasfilename", Mock(return_value=""))

    result = save_as(user_prefs, None, src_file)

    assert result is None
    assert not user_prefs.set.called


@pytest.mark.unit
def test_copies_file_and_updates_prefs_on_success(user_prefs, monkeypatch, tmp_path):
    src_file = tmp_path / "source.csv"
    src_file.write_bytes(b"hello world")
    dest_file = tmp_path / "chosen" / "dest.csv"
    dest_file.parent.mkdir()
    monkeypatch.setattr("Pytre_Client.save_as.filedialog.asksaveasfilename", Mock(return_value=str(dest_file)))

    result = save_as(user_prefs, None, src_file)

    assert result == dest_file
    assert dest_file.read_bytes() == b"hello world"
    user_prefs.set.assert_called_once_with(PrefsEnum.save_as_folder, dest_file.parent)


@pytest.mark.unit
def test_dialog_receives_initial_dir_and_filename_from_source(user_prefs, monkeypatch, tmp_path):
    src_file = tmp_path / "my_export.csv"
    src_file.write_text("data")
    asksaveasfilename = Mock(return_value="")
    monkeypatch.setattr("Pytre_Client.save_as.filedialog.asksaveasfilename", asksaveasfilename)

    save_as(user_prefs, None, src_file)

    user_prefs.get.assert_called_once_with(PrefsEnum.save_as_folder)
    _, kwargs = asksaveasfilename.call_args
    assert kwargs["initialdir"] == user_prefs.get.return_value
    assert kwargs["initialfile"] == "my_export.csv"
