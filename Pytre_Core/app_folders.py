"""
Folders of the application (install, resources, user's own files), names of the files it creates in the user's
folder, and preparation of that folder at client startup. Kept apart, with no dependency on the rest of the
package, so every module can import it.
"""

import os
import sys
from pathlib import Path


# ------------------------------------------------------------------------------------------
# Files the app creates in the user's config folder
# ------------------------------------------------------------------------------------------
SETTINGS_FILENAME: str = "Pytre_Settings.json"
REMOTES_FILENAME: str = "Pytre_Remotes.json"
USER_DB_FILENAME: str = "Pytre_Logs.db"
CACHE_FOLDER_NAME: str = ".temp_cache"

USER_FILES: tuple[str, ...] = (
    SETTINGS_FILENAME,
    REMOTES_FILENAME,
    USER_DB_FILENAME,
    f"{USER_DB_FILENAME}-journal",
    f"{USER_DB_FILENAME}-wal",
    f"{USER_DB_FILENAME}-shm",
    CACHE_FOLDER_NAME,
)


# ------------------------------------------------------------------------------------------
# Folder paths
# ------------------------------------------------------------------------------------------
def get_install_folder() -> Path:
    """Folder of the application data files (settings database, key file, update policy).
    When frozen this is the executable's folder (the working directory can be different) otherwise the
    working directory (sys.executable would be the Python interpreter)."""
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()


def get_resources_folder() -> Path:
    """Folder for the resources shipped with the application (icons, logo, license, blank database).
    When frozen PyInstaller extracts them to a temporary folder (_MEIPASS), see the "datas" of the .spec files."""
    root = Path(getattr(sys, "_MEIPASS")) if getattr(sys, "frozen", False) else Path(__file__).parent
    return root / "res"


def get_config_folder() -> Path:
    """Folder of the user's own files (preferences, remotes, local logs, cache): the platform's one, see
    _platform_config_folder(), or the former ~/Pytre as long as it couldn't be moved there."""
    config_folder = _platform_config_folder()
    legacy_folder = _legacy_config_folder()

    if not config_folder.exists() and legacy_folder.is_dir():
        return legacy_folder  # not moved yet, e.g. the migration failed: used as is until it succeeds
    return config_folder


def get_cache_folder() -> Path:
    """Folder of the query results extracted for the user, emptied of its old files at each start"""
    return get_config_folder() / CACHE_FOLDER_NAME


def _platform_config_folder() -> Path:
    """In the platform's local application data: %LOCALAPPDATA% on Windows (never roams with the profile),
    ~/Library/Application Support on macOS, $XDG_DATA_HOME (~/.local/share by default) elsewhere."""
    if sys.platform == "win32":
        base = _absolute_env_path("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = _absolute_env_path("XDG_DATA_HOME") or Path.home() / ".local" / "share"

    return base / "Pytre"


def _legacy_config_folder() -> Path:
    """Former folder of the user's own files, moved to _platform_config_folder() at client startup"""
    return Path.home() / "Pytre"


def _absolute_env_path(name: str) -> Path | None:
    """Path held by environment variable `name`, None if unset, empty or relative"""
    value = os.environ.get(name, "")
    return Path(value) if value and Path(value).is_absolute() else None


# ------------------------------------------------------------------------------------------
# User folders preparation, at client startup
# ------------------------------------------------------------------------------------------
class UserFolderError(Exception):
    """Raised when the user's folders can't be moved or created"""


def prepare_user_folders() -> None:
    """Moves the former user folder if needed, then creates the configuration and cache folders.
    Raises UserFolderError."""
    migrate_legacy_folders()

    cache_folder = get_cache_folder()
    try:
        cache_folder.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise UserFolderError(f"Couldn't create {cache_folder}: {e}") from e


def migrate_legacy_folders() -> None:
    """Moves the former user folder to _platform_config_folder() when only the former one exists. When the
    executable lives in the former folder, only the user's files (USER_FILES) are moved. Raises UserFolderError."""
    config_folder = _platform_config_folder()
    legacy_folder = _legacy_config_folder()

    if not legacy_folder.is_dir() or config_folder.exists():
        return  # nothing to move, or already moved

    try:
        config_folder.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise UserFolderError(f"Couldn't create {config_folder.parent}: {e}") from e

    if get_install_folder().resolve().is_relative_to(legacy_folder.resolve()):
        # move files individually since install folder is the legacy folder
        _move_entries(legacy_folder, config_folder, [legacy_folder / name for name in USER_FILES])
        return

    try:
        legacy_folder.rename(config_folder)  # everything is moved or nothing is
    except OSError:
        # try to individually move what is possible
        _move_entries(legacy_folder, config_folder, [legacy_folder / name for name in USER_FILES])


def _move_entries(legacy_folder: Path, config_folder: Path, entries: list[Path]) -> None:
    """Moves the existing `entries` of `legacy_folder` one by one to `config_folder`, created for them.
    On failure the moved ones are put back and `config_folder` removed, so the next start tries again.
    Raises UserFolderError."""
    moved: list[tuple[Path, Path]] = []
    try:
        config_folder.mkdir()
        for entry in entries:
            if entry.exists():
                target = config_folder / entry.name
                entry.rename(target)
                moved.append((entry, target))
    except OSError as e:
        not_restored = [str(target) for source, target in reversed(moved) if not _try_rename(target, source)]

        try:
            config_folder.rmdir()  # only if empty: never removes a file that couldn't be put back
        except OSError:
            pass

        msg = f"Couldn't move {legacy_folder} to {config_folder}: {e}"
        if not_restored:
            msg += f"\nPutting back already moved files failed, still in {config_folder}: {', '.join(not_restored)}"

        raise UserFolderError(msg) from e


def _try_rename(source: Path, target: Path) -> bool:
    try:
        source.rename(target)
        return True
    except OSError:
        return False
