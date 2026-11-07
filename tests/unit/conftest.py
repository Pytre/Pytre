"""
Fixtures applied to every unit test. Integration tests don't need them: the server never opens the user's files.
"""

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from Pytre_Core import app_folders


@dataclass(frozen=True)
class UserFolders:
    home: Path
    legacy: Path  # former folder of the user's files, ~/Pytre
    config: Path  # new folder of the user's files on the running platform


@pytest.fixture(autouse=True)
def isolated_user_folders(tmp_path_factory, monkeypatch) -> UserFolders:
    """Points the home and application data folders to a temporary folder: no test can read, write or move
    the real user's files (preferences, remotes, logs, cache), whatever the platform. Kept out of tmp_path,
    which tests may expect empty."""
    home = tmp_path_factory.mktemp("home")

    # Tk looks for the X server credentials in ~/.Xauthority when XAUTHORITY isn't set
    real_xauthority = Path.home() / ".Xauthority"
    if "XAUTHORITY" not in os.environ and real_xauthority.exists():
        monkeypatch.setenv("XAUTHORITY", str(real_xauthority))

    monkeypatch.setenv("HOME", str(home))  # Path.home() on Linux and macOS
    monkeypatch.setenv("USERPROFILE", str(home))  # Path.home() on Windows
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))

    return UserFolders(home=home, legacy=home / "Pytre", config=app_folders._platform_config_folder())
