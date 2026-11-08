"""
Tests for Pytre_Core/app_folders.py
"""

import sys
from pathlib import Path

import pytest

from Pytre_Core import app_folders
from Pytre_Core.app_folders import (
    UserFolderError,
    get_cache_folder,
    get_config_folder,
    get_install_folder,
    get_resources_folder,
    migrate_legacy_folders,
    prepare_user_folders,
)


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """Simulates a PyInstaller build: executable in tmp_path/install, resources extracted to tmp_path/meipass"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "install" / "Pytre_Client.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "meipass"), raising=False)
    return tmp_path


# ================================================================================
# get_install_folder
# ================================================================================


@pytest.mark.unit
def test_install_folder_is_the_executable_folder_once_frozen(frozen, monkeypatch, tmp_path):
    """Whatever the working directory: it depends on how the application was launched"""
    monkeypatch.chdir(tmp_path)

    assert get_install_folder() == frozen / "install"


@pytest.mark.unit
def test_install_folder_is_the_working_directory_when_not_frozen(monkeypatch, tmp_path):
    """Not the executable's folder: sys.executable is then the Python interpreter of the venv"""
    monkeypatch.chdir(tmp_path)

    assert get_install_folder() == tmp_path
    assert get_install_folder() != Path(sys.executable).parent


# ================================================================================
# get_resources_folder
# ================================================================================


@pytest.mark.unit
def test_resources_folder_is_extracted_by_pyinstaller_once_frozen(frozen):
    """Matches datas = [('Pytre_Core/res', 'res')] of the .spec files"""
    assert get_resources_folder() == frozen / "meipass" / "res"


@pytest.mark.unit
def test_resources_folder_is_the_package_res_folder_when_not_frozen():
    folder = get_resources_folder()

    assert folder == Path(app_folders.__file__).parent / "res"
    assert (folder / "app.png").is_file()


# ================================================================================
# get_config_folder
# ================================================================================


@pytest.mark.unit
def test_config_folder_is_in_local_app_data_on_windows(monkeypatch, tmp_path):
    """Local, not Roaming: SQLite logs, encrypted secrets and cache must not travel with the profile"""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))

    assert get_config_folder() == tmp_path / "Local" / "Pytre"


@pytest.mark.unit
@pytest.mark.parametrize("value", ["", "relative/AppData"], ids=["empty", "relative"])
def test_config_folder_falls_back_to_the_profile_without_a_usable_local_app_data(monkeypatch, value):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", value)

    assert get_config_folder() == Path.home() / "AppData" / "Local" / "Pytre"


@pytest.mark.unit
def test_config_folder_is_in_application_support_on_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")

    assert get_config_folder() == Path.home() / "Library" / "Application Support" / "Pytre"


@pytest.mark.unit
def test_config_folder_follows_xdg_data_home_on_linux(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    assert get_config_folder() == tmp_path / "data" / "Pytre"


@pytest.mark.unit
@pytest.mark.parametrize("value", [None, "", "relative/share"], ids=["unset", "empty", "relative"])
def test_config_folder_defaults_to_local_share_on_linux(monkeypatch, value):
    """The XDG specification says to ignore a relative XDG_DATA_HOME"""
    monkeypatch.setattr(sys, "platform", "linux")
    if value is None:
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_DATA_HOME", value)

    assert get_config_folder() == Path.home() / ".local" / "share" / "Pytre"


@pytest.mark.unit
def test_config_folder_does_not_depend_on_the_build(monkeypatch, frozen):
    expected = get_config_folder()
    monkeypatch.delattr(sys, "frozen")

    assert get_config_folder() == expected


# ================================================================================
# get_cache_folder
# ================================================================================


@pytest.mark.unit
def test_cache_folder_is_inside_the_config_folder():
    """Extracted query results belong to the user, next to their other files"""
    assert get_cache_folder().parent == get_config_folder()


# ================================================================================
# User folders preparation
# ================================================================================


def make_legacy_folder(folders) -> None:
    """Former folder as left by previous versions: preferences, remotes, local logs and cached extracts"""
    folders.legacy.mkdir()
    (folders.legacy / "Pytre_Settings.json").write_text("{}", encoding="utf-8")
    (folders.legacy / "Pytre_Remotes.json").write_text("{}", encoding="utf-8")
    (folders.legacy / "Pytre_Logs.db").write_bytes(b"sqlite")
    (folders.legacy / ".temp_cache").mkdir()
    (folders.legacy / ".temp_cache" / "query_20261107.csv").write_text("a;b", encoding="utf-8")


@pytest.mark.unit
def test_migration_moves_the_former_folder_with_all_its_files(isolated_user_folders):
    folders = isolated_user_folders
    make_legacy_folder(folders)

    migrate_legacy_folders()

    assert not folders.legacy.exists()
    for name in ("Pytre_Settings.json", "Pytre_Remotes.json", "Pytre_Logs.db", ".temp_cache/query_20261107.csv"):
        assert (folders.config / name).is_file(), name


@pytest.mark.unit
def test_migration_does_nothing_without_former_folder(isolated_user_folders):
    """New installation, or folder already moved by a previous start"""
    migrate_legacy_folders()

    assert not isolated_user_folders.config.exists()


@pytest.mark.unit
def test_migration_leaves_both_folders_untouched_when_both_exist(isolated_user_folders):
    """e.g. an older version started since then recreated ~/Pytre: the current folder is kept, nothing is lost"""
    folders = isolated_user_folders
    make_legacy_folder(folders)
    folders.config.mkdir(parents=True)
    (folders.config / "Pytre_Settings.json").write_text('{"last_server": "CURRENT"}', encoding="utf-8")

    migrate_legacy_folders()

    assert (folders.legacy / "Pytre_Settings.json").read_text(encoding="utf-8") == "{}"
    assert (folders.config / "Pytre_Settings.json").read_text(encoding="utf-8") == '{"last_server": "CURRENT"}'


@pytest.mark.unit
def test_migration_failure_raises_and_leaves_the_former_folder_intact(isolated_user_folders, monkeypatch):
    """e.g. a file still open by another instance of Pytre on Windows"""
    folders = isolated_user_folders
    make_legacy_folder(folders)

    def locked_rename(self, target):
        raise PermissionError("file in use")

    monkeypatch.setattr(Path, "rename", locked_rename)

    with pytest.raises(UserFolderError, match="file in use"):
        migrate_legacy_folders()

    assert (folders.legacy / "Pytre_Settings.json").is_file()
    assert not folders.config.exists()


@pytest.mark.unit
def test_prepare_user_folders_creates_config_and_cache_folders(isolated_user_folders):
    prepare_user_folders()

    assert get_cache_folder().is_dir()
    assert get_cache_folder().parent == isolated_user_folders.config


@pytest.mark.unit
def test_prepare_user_folders_migrates_the_former_folder(isolated_user_folders):
    folders = isolated_user_folders
    make_legacy_folder(folders)

    prepare_user_folders()

    assert not folders.legacy.exists()
    assert (folders.config / "Pytre_Settings.json").is_file()
    assert get_cache_folder().is_dir()


@pytest.mark.unit
def test_prepare_user_folders_raises_when_folders_cant_be_created(monkeypatch):
    def denied_mkdir(self, *args, **kwargs):
        raise PermissionError("access denied")

    monkeypatch.setattr(Path, "mkdir", denied_mkdir)

    with pytest.raises(UserFolderError, match="access denied"):
        prepare_user_folders()


@pytest.mark.unit
def test_config_folder_stays_the_former_one_until_it_is_moved(isolated_user_folders):
    """If the migration failed, the client keeps working with the user's files where they are"""
    folders = isolated_user_folders
    make_legacy_folder(folders)

    assert get_config_folder() == folders.legacy

    migrate_legacy_folders()

    assert get_config_folder() == folders.config


@pytest.mark.unit
@pytest.mark.parametrize("exe_folder", [".", "bin"], ids=["in the former folder", "in a subfolder"])
def test_migration_leaves_the_executable_and_its_files_in_place(isolated_user_folders, monkeypatch, exe_folder):
    """Only the user's files move: the executable, its update policy, settings database and onedir files stay"""
    folders = isolated_user_folders
    make_legacy_folder(folders)
    install = folders.legacy / exe_folder
    install.mkdir(exist_ok=True)
    app_files = ["Pytre_Client.exe", "Pytre.policy.json", "Pytre.db", "Pytre.key", "_internal_2.0.0/python314.dll"]
    for name in app_files:
        (install / name).parent.mkdir(parents=True, exist_ok=True)
        (install / name).write_text("app", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(install / "Pytre_Client.exe"))

    migrate_legacy_folders()

    for name in app_files:
        assert (install / name).is_file(), name
    for name in ("Pytre_Settings.json", "Pytre_Remotes.json", "Pytre_Logs.db", ".temp_cache/query_20261107.csv"):
        assert (folders.config / name).exists(), name
        assert not (folders.legacy / name).exists(), name


def refuse_renames(monkeypatch, refused) -> None:
    """Path.rename raises PermissionError for the paths matched by `refused(path)`, works for the others"""
    original_rename = Path.rename

    def rename(self, target):
        if refused(self):
            raise PermissionError(f"file in use: {self.name}")
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", rename)


@pytest.mark.unit
def test_migration_moves_the_user_files_one_by_one_when_the_folder_cant_be_renamed(isolated_user_folders, monkeypatch):
    """e.g. Windows refuses to rename the former folder while it is the working folder of a process"""
    folders = isolated_user_folders
    make_legacy_folder(folders)
    (folders.legacy / "notes.txt").write_text("not created by Pytre", encoding="utf-8")
    refuse_renames(monkeypatch, lambda path: path == folders.legacy)

    migrate_legacy_folders()

    assert (folders.config / "Pytre_Settings.json").is_file()
    assert (folders.config / ".temp_cache" / "query_20261107.csv").is_file()
    assert (folders.legacy / "notes.txt").is_file()  # unknown files are left where they are


@pytest.mark.unit
def test_migration_puts_moved_files_back_when_one_is_locked(isolated_user_folders, monkeypatch):
    """The next start must find everything in the former folder to try again"""
    folders = isolated_user_folders
    make_legacy_folder(folders)
    refuse_renames(monkeypatch, lambda path: path == folders.legacy or path.name == "Pytre_Logs.db")

    with pytest.raises(UserFolderError, match="Pytre_Logs.db"):
        migrate_legacy_folders()

    for name in ("Pytre_Settings.json", "Pytre_Remotes.json", "Pytre_Logs.db"):
        assert (folders.legacy / name).is_file(), name
    assert not folders.config.exists()
    assert get_config_folder() == folders.legacy


@pytest.mark.unit
def test_migration_failure_tells_which_files_couldnt_be_put_back(isolated_user_folders, monkeypatch):
    folders = isolated_user_folders
    make_legacy_folder(folders)
    remotes_moved = folders.config / "Pytre_Remotes.json"
    refuse_renames(
        monkeypatch,
        lambda path: path == folders.legacy or path.name == "Pytre_Logs.db" or path == remotes_moved,
    )

    with pytest.raises(UserFolderError) as raised:
        migrate_legacy_folders()

    assert str(remotes_moved) in str(raised.value)
    assert remotes_moved.is_file()
    assert (folders.legacy / "Pytre_Settings.json").is_file()  # put back
